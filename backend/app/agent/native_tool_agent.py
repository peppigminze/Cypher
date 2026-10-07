"""
Native tool-calling agent loop, using the model's real OpenAI-format
function-calling instead of CypherAgent's hand-rolled JSON-envelope
protocol in app/agent/core.py (kept as-is, untouched, for the existing
message pipeline). This is a separate, additive path for tool-capable
local models (Qwen2.5 and newer) that support the `tools` request field
natively - no prompt hacking, no regex-parsing the model's raw text.

Strict separation of concerns:
    - the LLM only ever decides WHICH tool to call and with what
      arguments (via `tool_calls` in its response message);
    - the backend (ToolExecutor) is the only thing that actually RUNS a
      tool - and only ever a SAFE one automatically;
    - the tool's result is fed back to the LLM as a "tool" role message,
      and the LLM produces the final natural-language answer from that.

Approval gate (stabilization pass, see app/tools/pending_approvals.py):
a tool call for anything that isn't ToolSecurity.SAFE (currently just
write_file) does NOT execute. The loop pauses, stashes everything needed
to resume in pending_approvals, and returns a ToolCallingResult with
`pending_confirmation` set instead of a normal reply. The caller
(app/api/tool_chat.py's /api/tool-chat/confirm) resumes via `resume()`
once the human approves or rejects it. A BLOCKED tool (none registered
yet) is rejected outright with an error observation, never offered for
confirmation - "must never run automatically" per app/tools/base.py
means no is not negotiable.

This intentionally does NOT reuse the OLD pipeline's PendingAction
SQLite model/`/api/actions` endpoints (app/database/models.py,
app/api/actions.py) even though the concept is the same - those are
wired specifically to resume app/agent/core.py's CypherAgent, and
reusing them here would couple the two independent pipelines together.
A future risk-tier system (see personalJarvis's Tool protocol:
safe/monitor/ask/block per tool, with per-call overrides) could unify
this later, but that's out of scope for this stabilization pass - see
the class-level TODO below.

Bounded by MAX_STEPS so a model that keeps calling tools can't loop
forever.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.llm.base import LLMProvider
from app.persona import CYPHER_PERSONA
from app.settings_store import record_token_usage
from app.tools import pending_approvals
from app.tools.base import ToolResult, ToolSecurity
from app.tools.pending_approvals import PendingApproval
from app.tools.registry import ToolRegistry
from app.tools.tool_executor import ToolExecutor, to_openai_tools

logger = logging.getLogger("cypher.agent.tool_calling")

MAX_STEPS = 5

SYSTEM_PROMPT = CYPHER_PERSONA + (
    "\nUse the available tools whenever they help you answer accurately "
    "instead of guessing (e.g. call get_current_time rather than assuming "
    "what time it is). Call at most one tool per step, look at its result, "
    "and only call another tool if you still need more information. Once "
    "you have enough information, reply to the user directly in plain "
    "text, following the tone/length rules above - do not describe the "
    "tool call itself, just answer the question."
)

# The tools this loop exposes to the model. Kept as an explicit allowlist
# (rather than every tool in the registry) so this demo path never
# accidentally hands the model destructive/PC-control tools it wasn't
# scoped for. Phase 6 adds three SAFE, low-risk PC-control tools
# (media_control, find_files, get_system_info) - see app/tools/
# media_tools.py and search_tools.py.
DEFAULT_TOOL_NAMES = [
    "get_current_time",
    "read_file",
    "write_file",
    "web_search",
    "media_control",
    "find_files",
    "get_system_info",
]


@dataclass
class ToolCallLogEntry:
    name: str
    arguments: Dict[str, Any]
    success: bool
    data: Any = None
    error: Optional[str] = None


@dataclass
class PendingToolCall:
    """Returned in place of a normal reply when a tool needs human
    confirmation before it can run. `arguments` is exposed as structured
    data for a UI to render - it is deliberately NOT read out via TTS
    (see `reply`, which stays a short, generic spoken prompt)."""

    approval_id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ToolCallingResult:
    reply: str
    tool_calls: List[ToolCallLogEntry] = field(default_factory=list)
    steps_used: int = 0
    pending_confirmation: Optional[PendingToolCall] = None


def _tool_message(tool_call_id: str, result: ToolResult) -> Dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps({"success": result.success, "data": result.data, "error": result.error}, default=str),
    }


class NativeToolCallingAgent:
    # TODO(risk-tiers): ToolSecurity today is a flat SAFE/CONFIRM_REQUIRED/
    # BLOCKED enum per tool, so "confirm" is all-or-nothing. personalJarvis's
    # reference Tool protocol supports per-call risk overrides (e.g. a
    # Gmail tool that's "safe" to read but "ask" to send) via
    # risk_tier_for_args(args). If more CONFIRM_REQUIRED tools get exposed
    # to this pipeline later, consider that pattern instead of a second
    # flat enum - not needed yet for write_file alone.
    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        tool_names: Optional[List[str]] = None,
        provider_name: str = "local",
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.tool_names = tool_names or DEFAULT_TOOL_NAMES
        self.executor = ToolExecutor(tools)
        # "local" or "cloud" - which provider `llm` actually is, purely
        # for Phase 7's per-provider token usage tracking (see
        # record_token_usage() in _loop below). The caller
        # (app/api/tool_chat.py) already knows this since it just chose
        # the provider - passed in rather than re-derived via isinstance.
        self.provider_name = provider_name

    async def run(self, db: Session | None, user_message: str) -> ToolCallingResult:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        return await self._loop(db, messages, call_log=[], start_step=1)

    async def resume(self, db: Session | None, approval: PendingApproval, approved: bool) -> ToolCallingResult:
        """Continue a paused run after the human approved or rejected the
        tool call named in `approval` (see app/api/tool_chat.py's
        /api/tool-chat/confirm)."""
        messages = approval.messages
        call_log = approval.call_log

        if approved:
            result = await self.executor.execute(approval.tool_name, approval.arguments, db=db)
        else:
            logger.info("TOOL CALL REJECTED BY USER -> name=%s arguments=%s", approval.tool_name, approval.arguments)
            result = ToolResult(success=False, error="The user declined to run this tool.")

        call_log.append(
            ToolCallLogEntry(
                name=approval.tool_name,
                arguments=approval.arguments,
                success=result.success,
                data=result.data,
                error=result.error,
            )
        )
        messages.append(_tool_message(approval.tool_call_id, result))

        if approval.remaining_calls:
            pending = await self._process_tool_calls(db, messages, call_log, approval.remaining_calls, approval.steps_used)
            if pending is not None:
                return pending

        return await self._loop(db, messages, call_log, start_step=approval.steps_used + 1)

    async def _loop(
        self,
        db: Session | None,
        messages: List[Dict[str, Any]],
        call_log: List[ToolCallLogEntry],
        start_step: int,
    ) -> ToolCallingResult:
        tools_schema = to_openai_tools(self.tools, self.tool_names)

        for step in range(start_step, MAX_STEPS + 1):
            response = await self.llm.chat_with_tools(messages, tools=tools_schema)
            message = response.message
            messages.append(message)
            if db is not None:
                record_token_usage(db, self.provider_name, self.llm.model, response.usage)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return ToolCallingResult(reply=message.get("content") or "", tool_calls=call_log, steps_used=step)

            pending = await self._process_tool_calls(db, messages, call_log, tool_calls, step)
            if pending is not None:
                return pending

        logger.warning("NativeToolCallingAgent exceeded MAX_STEPS=%d without a final answer.", MAX_STEPS)
        return ToolCallingResult(
            reply="Ich konnte die Anfrage nicht innerhalb des Schritt-Limits abschließen.",
            tool_calls=call_log,
            steps_used=MAX_STEPS,
        )

    async def _process_tool_calls(
        self,
        db: Session | None,
        messages: List[Dict[str, Any]],
        call_log: List[ToolCallLogEntry],
        calls: List[Dict[str, Any]],
        step: int,
    ) -> Optional[ToolCallingResult]:
        """Execute `calls` in order, mutating `messages`/`call_log` in
        place. Stops and returns a paused ToolCallingResult the moment a
        CONFIRM_REQUIRED tool is hit - any calls after it in this same
        batch are stashed on the PendingApproval and processed on resume,
        so no tool_call_id from this model turn is ever left without a
        matching "tool" response message. Returns None once every call
        in `calls` has been handled (executed, blocked, or errored)."""
        for i, call in enumerate(calls):
            function = call.get("function", {})
            name = function.get("name", "")
            arguments = self.executor.parse_arguments(function.get("arguments", "{}"))
            tool = self.tools.get(name)

            if tool is not None and tool.security == ToolSecurity.BLOCKED:
                result = ToolResult(success=False, error=f"Tool '{name}' is blocked and cannot be run.")
                logger.info("TOOL CALL BLOCKED -> name=%s arguments=%s", name, arguments)
            elif tool is not None and not self.tools.is_executable_automatically(name):
                approval_id = pending_approvals.create(
                    tool_name=name,
                    arguments=arguments,
                    tool_call_id=call.get("id", ""),
                    messages=messages,
                    call_log=call_log,
                    steps_used=step,
                    remaining_calls=calls[i + 1 :],
                )
                logger.info(
                    "TOOL CALL PENDING APPROVAL -> name=%s arguments=%s approval_id=%s",
                    name,
                    arguments,
                    approval_id,
                )
                return ToolCallingResult(
                    reply=f'Ich möchte "{name}" ausführen. Bestätigst du das?',
                    tool_calls=list(call_log),
                    steps_used=step,
                    pending_confirmation=PendingToolCall(approval_id=approval_id, name=name, arguments=arguments),
                )
            else:
                # SAFE, or an unknown tool name - either way ToolExecutor
                # is the right place to run/log/error it (it already
                # handles "no such tool" as a normal failed ToolResult).
                result = await self.executor.execute(name, arguments, db=db)

            call_log.append(
                ToolCallLogEntry(name=name, arguments=arguments, success=result.success, data=result.data, error=result.error)
            )
            messages.append(_tool_message(call.get("id", ""), result))

        return None
