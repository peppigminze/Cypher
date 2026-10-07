"""
Timers and spoken reminders ("Stell einen Timer auf 10 Minuten",
"Erinnere mich in einer Stunde an die Wäsche").

Built on the existing Task model (a task with due_at + reminder_enabled is
a reminder; timers carry the tag "timer"), so they survive a restart and
show up in the task list. The scheduler (app/scheduler.py) fires them and
announces them over the event bus.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pydantic import Field
from sqlalchemy import select

from app import scheduler
from app.database.db import session_scope
from app.database.models import Task, TaskStatus
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool
from app.tools.system_tools import local_now

MAX_TIMER_SECONDS = 7 * 24 * 3600


def _spoken_duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} Stunde{'n' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} Minute{'n' if minutes != 1 else ''}")
    if secs and not hours:
        parts.append(f"{secs} Sekunde{'n' if secs != 1 else ''}")
    return " und ".join(parts) or "0 Sekunden"


def _create(seconds: int, label: str) -> Dict[str, Any]:
    due = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    with session_scope() as db:
        task = Task(title=label, due_at=due, reminder_enabled=True)
        task.tags = ["timer"]
        db.add(task)
        db.flush()
        task_id = task.id
    local_due = due.astimezone(local_now().tzinfo)
    return {"timer_id": task_id, "label": label, "seconds": seconds, "duration_text": _spoken_duration(seconds), "rings_at": local_due.strftime("%H:%M:%S")}


def _active_timers() -> list[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    out = []
    with session_scope() as db:
        stmt = select(Task).where(Task.status == TaskStatus.pending, Task.reminder_enabled.is_(True)).order_by(Task.due_at)
        for task in db.execute(stmt).scalars().all():
            if "timer" not in task.tags or task.due_at is None:
                continue
            due = task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=timezone.utc)
            remaining = int((due - now).total_seconds())
            if remaining <= 0:
                continue
            out.append({"timer_id": task.id, "label": task.title, "remaining_seconds": remaining, "remaining_text": _spoken_duration(remaining)})
    return out


def _cancel(timer_id: Optional[int], label: Optional[str]) -> list[Dict[str, Any]]:
    cancelled = []
    with session_scope() as db:
        stmt = select(Task).where(Task.status == TaskStatus.pending, Task.reminder_enabled.is_(True))
        for task in db.execute(stmt).scalars().all():
            if "timer" not in task.tags:
                continue
            if timer_id is not None and task.id != timer_id:
                continue
            if label and label.lower() not in task.title.lower():
                continue
            task.status = TaskStatus.cancelled
            cancelled.append({"timer_id": task.id, "label": task.title})
    return cancelled


class SetTimerArgs(ArgsModel):
    seconds: int = Field(..., ge=1, le=MAX_TIMER_SECONDS, description="Dauer in Sekunden (10 Minuten = 600, 1 Stunde = 3600)")
    label: Optional[str] = Field(None, max_length=120, description="Woran erinnert werden soll, z. B. 'Wäsche'")


@register_tool
class SetTimerTool(Tool):
    name = "set_timer"
    description = "Timer oder Erinnerung stellen, die nach einer Dauer klingelt und angesagt wird. Dauer immer in Sekunden umrechnen."
    Args = SetTimerArgs
    risk = RiskTier.CONTROL
    exposed_by_default = True

    async def execute(self, db=None, seconds: int = 0, label: Optional[str] = None, **_) -> ToolResult:
        data = await asyncio.to_thread(_create, int(seconds), (label or "Timer").strip())
        scheduler.wake()
        return ToolResult(success=True, data=data)

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Timer: {_spoken_duration(int(args.get('seconds', 0)))}" + (f" ({args['label']})" if args.get("label") else "")


@register_tool
class ListTimersTool(Tool):
    name = "list_timers"
    description = "Laufende Timer und wie lange sie noch dauern."
    risk = RiskTier.READ_ONLY
    exposed_by_default = True

    async def execute(self, db=None, **_) -> ToolResult:
        timers = await asyncio.to_thread(_active_timers)
        return ToolResult(success=True, data={"count": len(timers), "timers": timers})


class CancelTimerArgs(ArgsModel):
    timer_id: Optional[int] = Field(None, description="ID aus list_timers")
    label: Optional[str] = Field(None, description="Teil der Bezeichnung, z. B. 'Wäsche'")


@register_tool
class CancelTimerTool(Tool):
    name = "cancel_timer"
    description = "Einen laufenden Timer abbrechen (per ID oder Bezeichnung; ohne Angabe: alle Timer)."
    Args = CancelTimerArgs
    risk = RiskTier.CONTROL
    exposed_by_default = True

    async def execute(self, db=None, timer_id: Optional[int] = None, label: Optional[str] = None, **_) -> ToolResult:
        cancelled = await asyncio.to_thread(_cancel, timer_id, label)
        scheduler.wake()
        if not cancelled:
            return ToolResult(success=False, error="Kein passender laufender Timer gefunden.")
        return ToolResult(success=True, data={"cancelled": cancelled})
