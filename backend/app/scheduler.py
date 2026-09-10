"""
Reminder scheduler (project spec section 12).

Runs as a background asyncio task inside the FastAPI process (started
from the lifespan in app/main.py). Every SCHEDULER_INTERVAL_SECONDS it
looks for tasks whose due_at has passed and that haven't fired yet for
this occurrence, marks them notified (so the PWA can show a
notification via GET /api/tasks/due-reminders), and - if recurring -
schedules the next occurrence instead of the reminder just disappearing.

Being level-triggered (a plain "due_at <= now() AND not yet notified
for this occurrence" query) rather than an in-memory timer means
downtime is handled for free: if the backend was off when a reminder
was due, the very next tick after restart fires it immediately instead
of silently losing it ("PC startet -> Scheduler synchronisiert ->
verpasste Reminder werden korrekt behandelt", project spec section 12).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database.db import session_scope
from app.database.models import Task, TaskStatus, advance_due_date

logger = logging.getLogger("cypher.scheduler")

SCHEDULER_INTERVAL_SECONDS = 20


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite has no real timezone-aware storage - SQLAlchemy silently
    returns a naive datetime when reading a DateTime(timezone=True)
    column back, even though we always write UTC-aware values (see the
    same workaround in api/agent.py::system_status). Comparing that
    naive value against datetime.now(timezone.utc) would otherwise
    raise TypeError."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _is_due(task: Task, now: datetime) -> bool:
    due_at = _as_utc(task.due_at)
    if due_at is None or due_at > now:
        return False
    # Already fired for *this* occurrence? last_notified_at is only
    # cleared implicitly by due_at moving forward (advance_due_date), so
    # "notified before the current due_at" means it's stale from a
    # previous occurrence and this one hasn't fired yet.
    last_notified_at = _as_utc(task.last_notified_at)
    return last_notified_at is None or last_notified_at < due_at


def check_due_reminders() -> int:
    """One scheduler tick. Returns how many reminders fired. Synchronous
    (plain SQLAlchemy session) so it's trivially unit-testable without
    an event loop."""
    now = datetime.now(timezone.utc)
    fired = 0
    with session_scope() as db:
        stmt = select(Task).where(Task.reminder_enabled.is_(True), Task.status == TaskStatus.pending)
        for task in db.execute(stmt).scalars().all():
            if not _is_due(task, now):
                continue
            task.last_notified_at = now
            if task.recurrence != "none":
                task.due_at = advance_due_date(_as_utc(task.due_at), task.recurrence)
            fired += 1
            logger.info("Reminder fired for task %s ('%s').", task.id, task.title)
    return fired


async def run_forever(interval_seconds: float = SCHEDULER_INTERVAL_SECONDS) -> None:
    logger.info("Reminder scheduler started. Checking every %.0fs.", interval_seconds)
    while True:
        try:
            check_due_reminders()
        except Exception:  # noqa: BLE001
            logger.exception("Reminder scheduler tick failed.")
        await asyncio.sleep(interval_seconds)
