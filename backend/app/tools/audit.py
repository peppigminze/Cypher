"""
Audit log of tool decisions (SQLite table `tool_audit`).

Arguments are stored truncated and with secret-looking values redacted;
results only as a short preview. Writing happens in a worker thread so a
slow disk never stalls the event loop, and an audit failure is logged
loudly but never breaks the conversation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.context import current_pipeline_id, current_request_id
from app.database.models import ToolAuditEntry

logger = logging.getLogger("cypher.tools.audit")

_MAX_ARGS_CHARS = 2000
_MAX_PREVIEW_CHARS = 500
_SECRET_KEY_RE = re.compile(r"(token|password|passwort|secret|api[_-]?key|credential)", re.IGNORECASE)


def redact(args: Dict[str, Any]) -> Dict[str, Any]:
    return {k: ("***" if _SECRET_KEY_RE.search(k) else v) for k, v in (args or {}).items()}


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class AuditRecord:
    tool: str
    decision: str
    risk: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    success: Optional[bool] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    result: Any = None
    approval_id: Optional[str] = None
    conversation_id: Optional[str] = None
    source: Optional[str] = None
    provider: Optional[str] = None


class AuditLog:
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None) -> None:
        if session_factory is None:
            from app.database.db import SessionLocal  # noqa: PLC0415

            session_factory = SessionLocal
        self._session_factory = session_factory

    def _row(self, rec: AuditRecord) -> ToolAuditEntry:
        preview = None
        if rec.result is not None:
            preview = _truncate(json.dumps(rec.result, ensure_ascii=False, default=str), _MAX_PREVIEW_CHARS)
        return ToolAuditEntry(
            request_id=current_request_id() if current_request_id() != "-" else None,
            pipeline_id=current_pipeline_id() if current_pipeline_id() != "-" else None,
            conversation_id=rec.conversation_id,
            source=rec.source,
            provider=rec.provider,
            tool=rec.tool,
            risk=rec.risk,
            decision=rec.decision,
            approval_id=rec.approval_id,
            summary=rec.summary,
            arguments_json=_truncate(json.dumps(redact(rec.arguments or {}), ensure_ascii=False, default=str), _MAX_ARGS_CHARS),
            success=rec.success,
            error=_truncate(rec.error, _MAX_PREVIEW_CHARS) if rec.error else None,
            duration_ms=rec.duration_ms,
            result_preview=preview,
        )

    def write_sync(self, rec: AuditRecord) -> None:
        row = self._row(rec)
        try:
            self._insert(row)
        except OperationalError as exc:
            # Self-heal a missing table (e.g. a database created before M2
            # without a restart in between) instead of losing the record.
            if "no such table" not in str(exc):
                logger.exception("AUDIT WRITE FAILED for tool=%s decision=%s", rec.tool, rec.decision)
                return
            try:
                with self._session_factory() as db:
                    ToolAuditEntry.__table__.create(bind=db.get_bind(), checkfirst=True)
                self._insert(self._row(rec))
            except Exception:  # noqa: BLE001
                logger.exception("AUDIT WRITE FAILED for tool=%s decision=%s", rec.tool, rec.decision)
        except Exception:  # noqa: BLE001 - auditing must never break a conversation
            logger.exception("AUDIT WRITE FAILED for tool=%s decision=%s", rec.tool, rec.decision)

    def _insert(self, row: ToolAuditEntry) -> None:
        with self._session_factory() as db:
            db.add(row)
            db.commit()

    async def write(self, rec: AuditRecord) -> None:
        # asyncio.to_thread copies contextvars, so the request/pipeline id
        # of the calling task still ends up in the row.
        logger.info("AUDIT tool=%s decision=%s risk=%s success=%s", rec.tool, rec.decision, rec.risk, rec.success)
        await asyncio.to_thread(self.write_sync, rec)

    def recent(self, limit: int = 100, tool: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._session_factory() as db:
            stmt = select(ToolAuditEntry).order_by(ToolAuditEntry.id.desc()).limit(limit)
            if tool:
                stmt = stmt.where(ToolAuditEntry.tool == tool)
            return [audit_entry_dict(row) for row in db.execute(stmt).scalars().all()]


def audit_entry_dict(row: ToolAuditEntry) -> Dict[str, Any]:
    ts: datetime = row.ts
    if ts is not None and ts.tzinfo is None:
        # SQLite drops the offset; values are always written in UTC.
        ts = ts.replace(tzinfo=timezone.utc)
    return {
        "id": row.id,
        "ts": ts.isoformat() if ts else None,
        "request_id": row.request_id,
        "conversation_id": row.conversation_id,
        "source": row.source,
        "provider": row.provider,
        "tool": row.tool,
        "risk": row.risk,
        "decision": row.decision,
        "approval_id": row.approval_id,
        "summary": row.summary,
        "arguments": json.loads(row.arguments_json or "{}") if row.arguments_json and row.arguments_json.endswith("}") else row.arguments_json,
        "success": row.success,
        "error": row.error,
        "duration_ms": row.duration_ms,
        "result_preview": row.result_preview,
    }


_default: Optional[AuditLog] = None


def get_audit_log() -> AuditLog:
    global _default
    if _default is None:
        _default = AuditLog()
    return _default


def set_audit_log(log: Optional[AuditLog]) -> None:
    global _default
    _default = log
