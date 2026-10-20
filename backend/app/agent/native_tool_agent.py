"""
Request/response facade over the streaming ConversationEngine
(app/agent/engine.py) for /api/tool-chat: runs a turn to completion and
returns the collected result. There is no second agent loop any more -
voice, text chat and this endpoint share the same engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.agent.engine import (
    REJECTED_REPLY,
    ApprovalRequired,
    ConversationEngine,
    ConversationStore,
    ToolFinished,
    TurnDone,
    TurnFailed,
    new_conversation_id,
)
from app.llm.base import LLMProvider
from app.persona import system_prompt
from app.tools.pending_approvals import PendingApproval
from app.tools.registry import ToolRegistry

# Re-exported for older imports/tests.
SYSTEM_PROMPT = system_prompt("text")
MAX_STEPS = 5
__all__ = ["NativeToolCallingAgent", "ToolCallingResult", "PendingToolCall", "ToolCallLogEntry", "REJECTED_REPLY", "SYSTEM_PROMPT"]


@dataclass
class ToolCallLogEntry:
    name: str
    arguments: Dict[str, Any]
    success: bool
    data: Any = None
    error: Optional[str] = None


@dataclass
class PendingToolCall:
    approval_id: str
    name: str
    arguments: Dict[str, Any]
    summary: str = ""
    risk: str = "write"
    expires_in_seconds: int = 0


@dataclass
class ToolCallingResult:
    reply: str
    tool_calls: List[ToolCallLogEntry] = field(default_factory=list)
    steps_used: int = 0
    pending_confirmation: Optional[PendingToolCall] = None


# One-shot requests get their own throwaway conversation history.
_oneshot_store = ConversationStore(max_conversations=20)


class NativeToolCallingAgent:
    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        tool_names: Optional[List[str]] = None,
        provider_name: str = "local",
        source: str = "text",
        conversation_id: Optional[str] = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.source = source
        self.conversation_id = conversation_id
        self.engine = ConversationEngine(
            llm,
            tools,
            provider_name=provider_name,
            tool_names=tool_names,
            store=None if conversation_id else _oneshot_store,
        )

    @property
    def executor(self):
        return self.engine.executor

    @property
    def tool_names(self) -> List[str]:
        return self.engine.tool_names

    async def run(self, db: Session | None, user_message: str) -> ToolCallingResult:
        conversation_id = self.conversation_id or new_conversation_id()
        return await self._collect(self.engine.run_turn(conversation_id, user_message, source=self.source, db=db))

    async def resume(self, db: Session | None, approval: PendingApproval, approved: bool) -> ToolCallingResult:
        return await self._collect(self.engine.resume(approval, approved, db=db))

    @staticmethod
    async def _collect(events) -> ToolCallingResult:
        log: List[ToolCallLogEntry] = []
        async for event in events:
            if isinstance(event, ToolFinished):
                log.append(ToolCallLogEntry(event.name, event.arguments, event.success, event.data, event.error))
            elif isinstance(event, ApprovalRequired):
                return ToolCallingResult(
                    reply=event.spoken_prompt,
                    tool_calls=log,
                    steps_used=event.steps_used,
                    pending_confirmation=PendingToolCall(event.approval_id, event.tool_name, event.arguments, event.summary, event.risk, event.expires_in_seconds),
                )
            elif isinstance(event, TurnDone):
                return ToolCallingResult(reply=event.reply, tool_calls=log, steps_used=event.steps_used)
            elif isinstance(event, TurnFailed):
                raise event.error
        return ToolCallingResult(reply="", tool_calls=log)
