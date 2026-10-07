"""
Reminder/timer scheduler.

Runs as a background asyncio task inside the backend. It is
level-triggered ("due_at <= now and not yet notified for this
occurrence"), so a reminder that came due while the PC was off still
fires right after the next start instead of being lost.

Precision: instead of polling every 20 s, it sleeps exactly until the
next due reminder (capped at SCHEDULER_INTERVAL_SECONDS) and can be woken
early when a new timer is created - a 90-second timer fires within about
a second, not up to 20 s late.

Fired reminders are published on the event bus ("reminder.due") so open
voice sessions can announce them; the PWA additionally polls
/api/tasks/due-reminders for browser notifications.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.core.events import bus
from app.database.db import session_scope
from app.database.models import Task, TaskStatus, advance_due_date

logger = logging.getLogger("cypher.scheduler")

SCHEDULER_INTERVAL_SECONDS = 20

_wake_event: Optional[asyncio.Event] = None
_wake_loop: Optional[asyncio.AbstractEventLoop] = None


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone-aware columns."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _is_due(task: Task, now: datetime) -> bool:
    due_at = _as_utc(task.due_at)
    if due_at is None or due_at > now:
        return False
    last_notified_at = _as_utc(task.last_notified_at)
    return last_notified_at is None or last_notified_at < due_at


def fire_due_reminders() -> List[Dict[str, Any]]:
    """One scheduler tick. Returns the reminders that fired."""
    now = datetime.now(timezone.utc)
    fired: List[Dict[str, Any]] = []
    with session_scope() as db:
        stmt = select(Task).where(Task.reminder_enabled.is_(True), Task.status == TaskStatus.pending)
        for task in db.execute(stmt).scalars().all():
            if not _is_due(task, now):
                continue
            task.last_notified_at = now
            if task.recurrence != "none":
                task.due_at = advance_due_date(_as_utc(task.due_at), task.recurrence)
            elif "timer" in task.tags:
                # A one-off timer is done once it rang.
                task.status = TaskStatus.completed
                task.completed_at = now
            fired.append({"id": task.id, "title": task.title, "is_timer": "timer" in task.tags})
            logger.info("Reminder fired for task %s ('%s').", task.id, task.title)
    for item in fired:
        bus.publish("reminder.due", **item)
    return fired


def check_due_reminders() -> int:
    """Backward-compatible wrapper: number of reminders that fired."""
    return len(fire_due_reminders())


def seconds_until_next_due() -> Optional[float]:
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        stmt = select(Task.due_at, Task.last_notified_at).where(
            Task.reminder_enabled.is_(True), Task.status == TaskStatus.pending, Task.due_at.is_not(None)
        )
        best: Optional[float] = None
        for due_at, last in db.execute(stmt).all():
            due = _as_utc(due_at)
            last = _as_utc(last)
            if due is None or (last is not None and last >= due):
                continue
            delta = max(0.0, (due - now).total_seconds())
            best = delta if best is None else min(best, delta)
        return best


def wake() -> None:
    """Re-plan the next sleep now (e.g. a new timer was created). Thread-safe."""
    event, loop = _wake_event, _wake_loop
    if event is None or loop is None or loop.is_closed():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    try:
        if running is loop:
            event.set()
        else:
            loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        # The scheduler's loop shut down in the meantime - nothing to wake.
        pass


async def run_forever(interval_seconds: float = SCHEDULER_INTERVAL_SECONDS) -> None:
    global _wake_event, _wake_loop
    _wake_event = asyncio.Event()
    _wake_loop = asyncio.get_running_loop()
    bus.bind_loop(_wake_loop)
    logger.info("Reminder scheduler started (max interval %.0fs).", interval_seconds)
    try:
        await _loop_forever(interval_seconds)
    finally:
        _wake_event = None
        _wake_loop = None


async def _loop_forever(interval_seconds: float) -> None:
    assert _wake_event is not None
    while True:
        sleep_for = interval_seconds
        try:
            await asyncio.to_thread(fire_due_reminders)
            next_due = await asyncio.to_thread(seconds_until_next_due)
            if next_due is not None:
                sleep_for = min(interval_seconds, max(0.2, next_due + 0.05))
        except Exception:  # noqa: BLE001 - the scheduler must keep running
            logger.exception("Reminder scheduler tick failed.")
        _wake_event.clear()
        try:
            await asyncio.wait_for(_wake_event.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass
