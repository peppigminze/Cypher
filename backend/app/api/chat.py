"""
Text chat on the conversation engine (M5) - the same agent as voice, but
answers stream back as Server-Sent Events (SSE) instead of audio.

    POST /api/chat            {"message", "conversation_id"?}   -> event stream
    POST /api/chat/approval   {"approval_id", "approved"}        -> event stream
    GET  /api/chat/history?conversation_id=...                   -> past turns (text)

Why SSE and not the WebSocket: a text chat needs no audio and no open
connection between turns; a plain HTTP request per message works through
every proxy, and the browser gets each word as soon as it exists.

Events - one JSON object per "data:" line, named like the voice protocol:
    conversation  {conversation_id}       always first
    reply_delta   {text}                  model tokens
    tool          {phase, name, summary, success?, error?}
    approval      {card}                  the turn pauses; answer via /api/chat/approval
    reply         {text, metrics}         the turn is done
    error         {code, message, hint}   German (app/core/errors.py)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.engine import (
    ApprovalRequired,
    ConversationEngine,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnDone,
    TurnFailed,
    conversations,
    new_conversation_id,
)
from app.auth import require_user
from app.config import get_settings
from app.core import errors
from app.database.db import SessionLocal
from app.llm.base import LLMUnavailableError
from app.llm.factory import get_provider_by_name
from app.security.rate_limit import rate_limit
from app.settings_store import get_active_provider
from app.tools import pending_approvals
from app.tools.catalog import build_registry

logger = logging.getLogger("cypher.api.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])

_CONVERSATION_ID = re.compile(r"^[a-f0-9]{8,32}$")
# Context lines the engine puts in front of the user's words (time, memory).
_CONTEXT_LINE = re.compile(r"^\[(Kontext|Aus deinem Gedächtnis|Vorlieben für deine Antwort)[^\n]*\]\n?", re.MULTILINE)


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[str] = None


class ApprovalIn(BaseModel):
    approval_id: str = Field(..., min_length=8, max_length=64)
    approved: bool


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _engine() -> ConversationEngine:
    """Engine for the currently selected provider (local/cloud toggle).
    Never falls back silently: a selected cloud without key is an error."""
    with SessionLocal() as db:
        provider_name = get_active_provider(db)
    llm = get_provider_by_name(provider_name)
    return ConversationEngine(llm, build_registry(), provider_name=provider_name, settings=get_settings(), store=conversations)


async def _stream(events: AsyncIterator[Any], conversation_id: str) -> AsyncIterator[str]:
    yield _sse({"type": "conversation", "conversation_id": conversation_id})
    try:
        async for event in events:
            if isinstance(event, TextDelta):
                yield _sse({"type": "reply_delta", "text": event.text})
            elif isinstance(event, ToolStarted):
                yield _sse({"type": "tool", "phase": "start", "name": event.name, "summary": event.summary})
            elif isinstance(event, ToolFinished):
                yield _sse({"type": "tool", "phase": "end", "name": event.name, "summary": event.summary, "success": event.success, "error": event.error})
            elif isinstance(event, ApprovalRequired):
                yield _sse({"type": "approval", "card": {
                    "approval_id": event.approval_id, "tool_name": event.tool_name, "arguments": event.arguments,
                    "summary": event.summary, "risk": event.risk, "expires_in_seconds": event.expires_in_seconds}})
            elif isinstance(event, TurnDone):
                yield _sse({"type": "reply", "text": event.reply, "metrics": event.metrics})
            elif isinstance(event, TurnFailed):
                yield _sse({"type": "error", **event.error.to_dict()})
    except Exception as exc:  # noqa: BLE001 - the stream must end with a readable error
        logger.exception("Chat turn crashed")
        yield _sse({"type": "error", **errors.from_exception(exc).to_dict()})
    finally:
        if hasattr(events, "aclose"):
            await events.aclose()  # records an interrupted answer if the client went away


def _event_stream(body: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(body, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("", dependencies=[Depends(rate_limit("chat"))])
async def chat(payload: ChatIn, _user=Depends(require_user)) -> StreamingResponse:
    cid = payload.conversation_id if payload.conversation_id and _CONVERSATION_ID.match(payload.conversation_id) else new_conversation_id()
    try:
        engine = _engine()
    except LLMUnavailableError as exc:
        err = errors.from_exception(exc)

        async def failed() -> AsyncIterator[str]:
            yield _sse({"type": "conversation", "conversation_id": cid})
            yield _sse({"type": "error", **err.to_dict()})

        return _event_stream(failed())
    db = SessionLocal()

    async def body() -> AsyncIterator[str]:
        try:
            async for chunk in _stream(engine.run_turn(cid, payload.message, source="text", db=db), cid):
                yield chunk
        finally:
            db.close()

    return _event_stream(body())


@router.post("/approval", dependencies=[Depends(rate_limit("approval"))])
async def resolve_approval(payload: ApprovalIn, _user=Depends(require_user)) -> StreamingResponse:
    approval = pending_approvals.pop(payload.approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Diese Bestätigung ist abgelaufen oder wurde schon beantwortet.")
    engine = ConversationEngine(get_provider_by_name(approval.provider or "local"), build_registry(), provider_name=approval.provider or "local", settings=get_settings(), store=conversations)
    db = SessionLocal()

    async def body() -> AsyncIterator[str]:
        try:
            async for chunk in _stream(engine.resume(approval, payload.approved, db=db), approval.conversation_id or ""):
                yield chunk
        finally:
            db.close()

    return _event_stream(body())


def display_text(content: str) -> str:
    """A stored user turn without the context lines the engine added."""
    return _CONTEXT_LINE.sub("", content or "").strip()


@router.get("/history")
def history(conversation_id: str, _user=Depends(require_user)) -> Dict[str, Any]:
    if not _CONVERSATION_ID.match(conversation_id or ""):
        raise HTTPException(status_code=422, detail="Ungültige Gesprächs-ID.")
    turns: List[Dict[str, str]] = []
    for msg in conversations.get(conversation_id).messages:
        if msg["role"] == "user":
            turns.append({"role": "user", "text": display_text(msg.get("content") or "")})
        elif msg["role"] == "assistant" and not msg.get("tool_calls") and msg.get("content"):
            turns.append({"role": "assistant", "text": msg["content"]})
    return {"conversation_id": conversation_id, "turns": turns}
