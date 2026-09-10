"""
Database models.

States (Message.status / used loosely for Task processing too):
    pending -> processing -> completed
                           -> failed
    (any state) -> cancelled
"""
from __future__ import annotations

import enum
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base, ensure_columns


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_due_at(value: str) -> datetime:
    """Parse an ISO-8601 datetime string, assuming UTC if no offset was
    given. Without this, a naive datetime (e.g. because the LLM omitted
    a timezone) would later crash the reminder scheduler when compared
    against the timezone-aware `datetime.now(timezone.utc)`."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class MessageStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    cancelled = "cancelled"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Message(Base):
    """A chat message sent by the user. Processed asynchronously by the
    local PC agent, which may run while the phone/client is offline."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Client-supplied idempotency key so the PWA offline-queue can retry
    # sends without creating duplicate messages.
    client_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus), default=MessageStatus.pending, nullable=False, index=True
    )
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Retry bookkeeping (project spec section 19). Added via an additive
    # migration (run_migrations() below) so existing messages survive.
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Set when a transient failure (e.g. local LLM unreachable) requeues
    # the message - GET /api/sync/pending skips it until this time has
    # passed, so a down Ollama doesn't get hammered every poll interval.
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # "local" or "cloud" - which LLM actually produced the final answer
    # (project spec section 21: the PWA shows 🖥️ Local / ☁️ Cloud so the
    # user can tell how a request was handled). None until the pipeline
    # has made at least one LLM call.
    processed_by: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Which tools ran while answering this message, JSON-encoded (same
    # shape as PendingAction.observations - {"tool", "arguments",
    # "result"|"error"}). Lets the PWA show short tool-activity status
    # lines (project spec section 29: "🔧 Searching files ✓ Found 4
    # files" - no chain-of-thought, just what ran and whether it
    # succeeded).
    observations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def observations(self) -> list:
        return json.loads(self.observations_json or "[]")

    @observations.setter
    def observations(self, value: list | None) -> None:
        self.observations_json = json.dumps(value or [], default=str)


# Plain strings (not a SQLAlchemy Enum/CHECK constraint), same reasoning
# as MEMORY_TYPES above: extending the list later must never require a
# schema migration.
RECURRENCE_VALUES = ("none", "daily", "weekly", "monthly")
DEFAULT_RECURRENCE = "none"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.pending, nullable=False, index=True
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority), default=TaskPriority.medium, nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Reminders/notes/tags (project spec sections 11/12). Added via an
    # additive migration (run_migrations() below), so existing tasks
    # rows get sane defaults instead of the insert/select failing.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # One of RECURRENCE_VALUES. "none" = fires once, like a one-off
    # reminder; anything else re-schedules due_at forward after firing
    # instead of the reminder disappearing (project spec section 12).
    recurrence: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DEFAULT_RECURRENCE, server_default=DEFAULT_RECURRENCE
    )
    # Set by the reminder scheduler (app/scheduler.py) the moment a due
    # reminder fires, so a restart after downtime ("PC startet -> Scheduler
    # synchronisiert -> verpasste Reminder werden korrekt behandelt", spec
    # section 12) never re-fires - or loses - the same occurrence twice.
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def tags(self) -> list[str]:
        return json.loads(self.tags_json or "[]")

    @tags.setter
    def tags(self, value: list[str] | None) -> None:
        self.tags_json = json.dumps(value or [])


def advance_due_date(due_at: datetime, recurrence: str) -> datetime:
    """Next occurrence of a recurring reminder after it fires.

    No dateutil dependency in this project, so month arithmetic is done
    by hand: same day-of-month next month, clamped to that month's
    actual length (so a reminder due Jan 31 becomes Feb 28/29, not an
    invalid date or a silent skip to March).
    """
    if recurrence == "daily":
        return due_at + timedelta(days=1)
    if recurrence == "weekly":
        return due_at + timedelta(days=7)
    if recurrence == "monthly":
        year = due_at.year + (due_at.month // 12)
        month = due_at.month % 12 + 1
        # Length of the target month, without pulling in the `calendar` module.
        if month == 12:
            days_in_month = 31
        else:
            next_month_first = datetime(year + (month // 12), month % 12 + 1, 1)
            days_in_month = (next_month_first - datetime(year, month, 1)).days
        day = min(due_at.day, days_in_month)
        return due_at.replace(year=year, month=month, day=day)
    raise ValueError(f"Unknown recurrence '{recurrence}'.")


# Deliberately a plain string (not a SQLAlchemy Enum/CHECK constraint) so
# a future addition to this list never requires a schema migration - see
# app/memory/store.py for how it's used/validated.
MEMORY_TYPES = ("fact", "preference", "project")
DEFAULT_MEMORY_TYPE = "fact"


class MemoryEntry(Base):
    """Long-term memory (project spec section 10). Short-term/conversation
    context is intentionally NOT stored here - it's just the recent
    Message rows for the active conversation, already available without
    a separate table. Only information Cypher decides is durably
    relevant (a fact, a stated preference, project knowledge) lands
    here via the save_memory tool or the /api/memory endpoint.
    """

    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # One of MEMORY_TYPES. Added via an additive migration (see
    # run_migrations() below) so existing memory_entries rows default
    # to "fact" instead of the insert failing or data being lost.
    memory_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DEFAULT_MEMORY_TYPE, server_default=DEFAULT_MEMORY_TYPE
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ActionStatus(str, enum.Enum):
    """Lifecycle of a CONFIRM_REQUIRED tool call the agent wants to run.

    awaiting_confirmation -> confirmed | rejected
    confirmed             -> executing -> completed | failed
    """

    awaiting_confirmation = "awaiting_confirmation"
    confirmed = "confirmed"
    rejected = "rejected"
    executing = "executing"
    completed = "completed"
    failed = "failed"


class PendingAction(Base):
    """A tool call the agent planned but must not run automatically
    (security = CONFIRM_REQUIRED, see app/tools/base.py). Created by the
    Cypher Agent Core when it pauses mid-pipeline; resolved by the human
    user via /api/actions/{id}/confirm|reject, then picked up and
    executed by the local PC agent, which resumes the paused pipeline.

    This is a new table, so it carries no migration risk for existing
    databases - ActionStatus's CHECK constraint is created fresh.
    """

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # JSON-encoded so we don't need a schema-per-tool table.
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Prior tool observations from this pipeline run, so execution can
    # resume a multi-step plan exactly where it paused.
    observations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[ActionStatus] = mapped_column(
        Enum(ActionStatus), default=ActionStatus.awaiting_confirmation, nullable=False, index=True
    )
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def arguments(self) -> dict:
        return json.loads(self.arguments_json or "{}")

    @arguments.setter
    def arguments(self, value: dict) -> None:
        self.arguments_json = json.dumps(value or {})

    @property
    def observations(self) -> list:
        return json.loads(self.observations_json or "[]")

    @observations.setter
    def observations(self, value: list) -> None:
        self.observations_json = json.dumps(value or [])

    @property
    def result(self):
        return json.loads(self.result_json) if self.result_json else None

    @result.setter
    def result(self, value) -> None:
        self.result_json = json.dumps(value, default=str) if value is not None else None


class AgentHeartbeat(Base):
    """Single-row-per-agent table used to determine PC online/offline
    status. In V1 there is exactly one agent ("default")."""

    __tablename__ = "agent_heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(64), unique=True, default="default")
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommandLog(Base):
    """Audit trail for every run_command execution (project spec section
    15: "command logging"). A brand-new table - no migration risk. Never
    delete rows here automatically; this is the record of what Cypher
    actually ran on the user's PC."""

    __tablename__ = "command_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    working_dir: Mapped[str] = mapped_column(Text, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base):
    """A project Cypher should know about (project spec section 28), so
    "arbeite an meinem Cypher-Projekt" resolves to real context (path,
    tech stack, repo) instead of the model guessing. A brand-new table -
    no migration risk."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    technologies_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    repository: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def technologies(self) -> list[str]:
        return json.loads(self.technologies_json or "[]")

    @technologies.setter
    def technologies(self, value: list[str] | None) -> None:
        self.technologies_json = json.dumps(value or [])


def run_migrations() -> None:
    """Additive, idempotent column migrations for tables that predate a
    field (see ensure_columns() in app/database/db.py). Called from
    init_db() after create_all(), so brand-new databases already have
    every column via the model definitions and this is a no-op for them.
    """
    ensure_columns(
        "memory_entries",
        {"memory_type": f"VARCHAR(32) NOT NULL DEFAULT '{DEFAULT_MEMORY_TYPE}'"},
    )
    ensure_columns(
        "tasks",
        {
            "notes": "TEXT",
            "tags_json": "TEXT NOT NULL DEFAULT '[]'",
            "reminder_enabled": "INTEGER NOT NULL DEFAULT 0",
            "recurrence": f"VARCHAR(16) NOT NULL DEFAULT '{DEFAULT_RECURRENCE}'",
            "last_notified_at": "DATETIME",
        },
    )
    ensure_columns(
        "messages",
        {
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "next_retry_at": "DATETIME",
            "processed_by": "VARCHAR(16)",
            "observations_json": "TEXT NOT NULL DEFAULT '[]'",
        },
    )
