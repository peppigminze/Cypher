from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import (
    RECURRENCE_VALUES,
    Task,
    TaskPriority,
    TaskStatus,
    parse_due_at,
    utcnow,
)
from app.tools.base import RiskTier, Tool, ToolResult, register_tool


@register_tool
class CreateTaskTool(Tool):
    name = "create_task"
    description = (
        "Create a new task/todo item, optionally with a due date, priority, notes, "
        "and tags. Set due_at plus reminder=true to make this a reminder Cypher will "
        "notify the user about when it comes due. Use recurrence for repeating "
        "reminders like 'every Friday' (weekly) or 'every day' (daily). Always "
        "compute due_at as an absolute ISO-8601 datetime relative to the current "
        "time given in the conversation context - never leave a relative phrase "
        "like 'tomorrow' unresolved."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "notes": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
            "due_at": {"type": "string", "format": "date-time"},
            "reminder": {"type": "boolean", "description": "Notify the user when due_at is reached."},
            "recurrence": {"type": "string", "enum": list(RECURRENCE_VALUES)},
        },
        "required": ["title"],
    }
    risk = RiskTier.CONTROL

    async def execute(
        self,
        db: Session,
        title: str,
        description: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        priority: str = "medium",
        due_at: str | None = None,
        reminder: bool = False,
        recurrence: str = "none",
        **_,
    ) -> ToolResult:
        try:
            due_dt = parse_due_at(due_at) if due_at else None
            if recurrence not in RECURRENCE_VALUES:
                recurrence = "none"
            task = Task(
                title=title,
                description=description,
                notes=notes,
                priority=TaskPriority(priority) if priority else TaskPriority.medium,
                due_at=due_dt,
                reminder_enabled=bool(reminder and due_dt is not None),
                recurrence=recurrence if due_dt is not None else "none",
            )
            task.tags = tags or []
            db.add(task)
            db.commit()
            db.refresh(task)
            return ToolResult(success=True, data={"id": task.id, "title": task.title})
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            return ToolResult(success=False, error=str(exc))


@register_tool
class ListTasksTool(Tool):
    name = "list_tasks"
    description = "List tasks, optionally filtered by status (pending/completed/cancelled)."
    parameters = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["pending", "completed", "cancelled"]}},
    }
    risk = RiskTier.READ_ONLY

    async def execute(self, db: Session, status: str | None = None, **_) -> ToolResult:
        stmt = select(Task).order_by(Task.created_at.desc())
        if status:
            stmt = stmt.where(Task.status == TaskStatus(status))
        tasks = db.execute(stmt).scalars().all()
        data = [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority.value,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "reminder": t.reminder_enabled,
                "recurrence": t.recurrence,
                "tags": t.tags,
            }
            for t in tasks
        ]
        return ToolResult(success=True, data=data)


@register_tool
class CompleteTaskTool(Tool):
    name = "complete_task"
    description = "Mark a task as completed by its id."
    parameters = {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}
    risk = RiskTier.CONTROL

    async def execute(self, db: Session, task_id: int, **_) -> ToolResult:
        task = db.get(Task, task_id)
        if task is None:
            return ToolResult(success=False, error=f"Task {task_id} not found.")
        task.status = TaskStatus.completed
        task.completed_at = utcnow()
        db.commit()
        return ToolResult(success=True, data={"id": task.id, "status": task.status.value})


@register_tool
class DeleteTaskTool(Tool):
    name = "delete_task"
    description = "Delete a task by its id."
    parameters = {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}
    # Destructive - the agent may never run this automatically (see
    # project spec section 14). The human user can still delete tasks
    # directly through the authenticated REST endpoint / UI; this only
    # blocks the LLM-driven autonomous tool call until a confirmation
    # UI exists (see project spec section 30/14).
    risk = RiskTier.DESTRUCTIVE

    async def execute(self, db: Session, task_id: int, **_) -> ToolResult:
        task = db.get(Task, task_id)
        if task is None:
            return ToolResult(success=False, error=f"Task {task_id} not found.")
        db.delete(task)
        db.commit()
        return ToolResult(success=True, data={"id": task_id, "deleted": True})
