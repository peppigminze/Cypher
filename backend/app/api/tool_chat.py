"""
Test endpoint for the native tool-calling agent (app/agent/native_tool_agent.py).

Separate from /api/messages, which still runs CypherAgent's existing
JSON-envelope pipeline (app/agent/core.py) untouched. This endpoint
exists so real OpenAI-format function-calling can be exercised and
verified end to end - against a tool-capable local model (Qwen2.5) or,
since Phase 7, an OpenAI-compatible cloud model - without touching the
production chat/message flow.

Provider selection (Phase 7): every request here uses whatever
app/settings_store.get_active_provider() currently says ("local" or
"cloud" - persisted in SQLite, changed only via
POST /api/settings/provider, see app/api/settings.py). Never inferred,
never automatic - a request never silently ends up on the cloud.

Approval gate (stabilization pass): a CONFIRM_REQUIRED tool (currently
just write_file) no longer executes immediately. /api/tool-chat can come
back with `pending_confirmation` set instead of a final reply - call
POST /api/tool-chat/confirm with that `approval_id` and `approved` to
let the tool run (or not) and get the continued reply. See
app/tools/pending_approvals.py and app/agent/native_tool_agent.py for
how the pause/resume state is kept. Unaffected by which provider is
active - both speak the identical tool-calling interface.
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.native_tool_agent import NativeToolCallingAgent, ToolCallingResult
from app.auth import require_user
from app.core import errors
from app.security.rate_limit import rate_limit
from app.database.db import get_db
from app.llm.base import LLMUnavailableError
from app.llm.factory import get_provider_by_name
from app.settings_store import get_active_provider
from app.tools import pending_approvals
from app.tools.defaults import build_default_registry

router = APIRouter(tags=["tool-chat"])


class ToolChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    # "voice" when the request comes from the voice UI (audit log, reply length).
    source: Literal["voice", "text"] = "text"


class ToolChatConfirmIn(BaseModel):
    approval_id: str
    approved: bool


class ToolCallOut(BaseModel):
    name: str
    arguments: dict
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class PendingConfirmationOut(BaseModel):
    approval_id: str
    tool_name: str
    arguments: dict
    summary: str = ""
    risk: str = "write"
    expires_in_seconds: int = 0


class ToolChatOut(BaseModel):
    reply: str
    tool_calls: List[ToolCallOut]
    steps_used: int
    pending_confirmation: Optional[PendingConfirmationOut] = None
    provider: str


def _build_agent(db: Session, source: str = "text") -> tuple[NativeToolCallingAgent, str]:
    provider_name = get_active_provider(db)
    try:
        llm = get_provider_by_name(provider_name)
    except LLMUnavailableError as exc:
        raise errors.cloud_not_configured() from exc
    registry = build_default_registry()
    agent = NativeToolCallingAgent(llm=llm, tools=registry, provider_name=provider_name, source=source)
    return agent, provider_name


def _to_response(result: ToolCallingResult, provider_name: str) -> ToolChatOut:
    return ToolChatOut(
        reply=result.reply,
        tool_calls=[
            ToolCallOut(name=tc.name, arguments=tc.arguments, success=tc.success, data=tc.data, error=tc.error)
            for tc in result.tool_calls
        ],
        steps_used=result.steps_used,
        pending_confirmation=(
            PendingConfirmationOut(
                approval_id=result.pending_confirmation.approval_id,
                tool_name=result.pending_confirmation.name,
                arguments=result.pending_confirmation.arguments,
                summary=result.pending_confirmation.summary,
                risk=result.pending_confirmation.risk,
                expires_in_seconds=result.pending_confirmation.expires_in_seconds,
            )
            if result.pending_confirmation
            else None
        ),
        provider=provider_name,
    )


@router.post("/api/tool-chat", response_model=ToolChatOut, dependencies=[Depends(rate_limit("chat"))])
async def tool_chat(
    payload: ToolChatIn,
    db: Session = Depends(get_db),
    _user=Depends(require_user),
) -> ToolChatOut:
    agent, provider_name = _build_agent(db, payload.source)

    try:
        result = await agent.run(db, payload.message)
    except LLMUnavailableError as exc:
        # German message + hint via the error catalog (app/core/errors.py).
        raise errors.from_exception(exc) from exc

    return _to_response(result, provider_name)


@router.post("/api/tool-chat/confirm", response_model=ToolChatOut, dependencies=[Depends(rate_limit("approval"))])
async def tool_chat_confirm(
    payload: ToolChatConfirmIn,
    db: Session = Depends(get_db),
    _user=Depends(require_user),
) -> ToolChatOut:
    approval = pending_approvals.pop(payload.approval_id)
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown or expired approval_id - the confirmation may have already been resolved or timed out.",
        )

    agent, provider_name = _build_agent(db, approval.source)

    try:
        result = await agent.resume(db, approval, payload.approved)
    except LLMUnavailableError as exc:
        # German message + hint via the error catalog (app/core/errors.py).
        raise errors.from_exception(exc) from exc

    return _to_response(result, provider_name)
