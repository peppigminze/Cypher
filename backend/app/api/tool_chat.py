"""
Test endpoint for the native tool-calling agent (app/agent/native_tool_agent.py).

Separate from /api/messages, which still runs CypherAgent's existing
JSON-envelope pipeline (app/agent/core.py) untouched. This endpoint
exists so real OpenAI-format function-calling against a tool-capable
local model (Qwen2.5) can be exercised and verified end to end, without
touching the production chat/message flow.

Approval gate (stabilization pass): a CONFIRM_REQUIRED tool (currently
just write_file) no longer executes immediately. /api/tool-chat can come
back with `pending_confirmation` set instead of a final reply - call
POST /api/tool-chat/confirm with that `approval_id` and `approved` to
let the tool run (or not) and get the continued reply. See
app/tools/pending_approvals.py and app/agent/native_tool_agent.py for
how the pause/resume state is kept.
"""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.native_tool_agent import NativeToolCallingAgent, ToolCallingResult
from app.auth import require_user
from app.database.db import get_db
from app.llm.base import LLMUnavailableError
from app.llm.factory import get_llm_provider
from app.llm.local_provider import LocalLLMProvider
from app.tools import pending_approvals
from app.tools.defaults import build_default_registry

router = APIRouter(tags=["tool-chat"])


class ToolChatIn(BaseModel):
    message: str


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


class ToolChatOut(BaseModel):
    reply: str
    tool_calls: List[ToolCallOut]
    steps_used: int
    pending_confirmation: Optional[PendingConfirmationOut] = None


def _get_local_llm() -> LocalLLMProvider:
    llm = get_llm_provider()
    if not isinstance(llm, LocalLLMProvider):
        # chat_with_tools() only exists on LocalLLMProvider - the cloud
        # fallback provider doesn't implement native tool-calling here.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Native tool-calling requires LLM_PROVIDER=local.",
        )
    return llm


def _to_response(result: ToolCallingResult) -> ToolChatOut:
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
            )
            if result.pending_confirmation
            else None
        ),
    )


@router.post("/api/tool-chat", response_model=ToolChatOut)
async def tool_chat(
    payload: ToolChatIn,
    db: Session = Depends(get_db),
    _user=Depends(require_user),
) -> ToolChatOut:
    llm = _get_local_llm()
    registry = build_default_registry()
    agent = NativeToolCallingAgent(llm=llm, tools=registry)

    try:
        result = await agent.run(db, payload.message)
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return _to_response(result)


@router.post("/api/tool-chat/confirm", response_model=ToolChatOut)
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

    llm = _get_local_llm()
    registry = build_default_registry()
    agent = NativeToolCallingAgent(llm=llm, tools=registry)

    try:
        result = await agent.resume(db, approval, payload.approved)
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return _to_response(result)
