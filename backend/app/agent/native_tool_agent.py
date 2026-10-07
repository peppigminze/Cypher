"""
Native tool-calling agent (OpenAI-format function calling through the
LLMProvider interface), used by /api/tool-chat.

Separation of concerns:
    - the LLM only PROPOSES which tool to call with which arguments;
    - app/tools/executor.py decides whether it runs (validation, risk tier,
      approval, taint) and runs it - the only execution path;
    - results go back to the LLM as role=tool messages; external content
      is wrapped as untrusted data (app/security/untrusted.py).

Approval gate: a call whose per-call risk is write/destructive pauses the
loop; the state is stored in app/tools/pending_approvals.py and resumed by
`resume()` after the user's decision. A rejection ends the turn with a
fixed reply - the model is not asked to phrase it, because it used to
answer "Erledigt." after a "Nein" (docs/overhaul/AUDIT.md H1).

Taint: once a tool returned untrusted content, the turn is "tainted" and
tools that must only follow a direct user request (memory writes,
clipboard writes) refuse to run - an injected instruction in a web page
cannot make Cypher remember or copy something.

Bounded by AGENT_MAX_STEPS so a model that keeps calling tools can't loop
forever.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.base import LLMProvider, parse_tool_arguments
from app.persona import CYPHER_PERSONA
from app.security.untrusted import UNTRUSTED_RULE, tool_result_message_content
from app.settings_store import record_token_usage
from app.tools import pending_approvals
from app.tools.base import ToolResult
from app.tools.catalog import exposed_tool_names
from app.tools.context import ToolContext
from app.tools.executor import ToolExecutor, to_openai_tools
from app.tools.pending_approvals import PendingApproval
from app.tools.registry import ToolRegistry
from app.tools.system_tools import german_date, local_now

logger = logging.getLogger("cypher.agent.tool_calling")

# Kept for older imports; the effective limit is AGENT_MAX_STEPS.
MAX_STEPS = 5

REJECTED_REPLY = "Okay, ich habe es nicht ausgeführt."

SYSTEM_PROMPT = (
    CYPHER_PERSONA
    + "\nNutze die verfügbaren Tools, wenn sie helfen, statt zu raten. Rufe höchstens ein Tool pro Schritt auf, "
    "sieh dir das Ergebnis an und rufe nur dann ein weiteres auf, wenn du noch Informationen brauchst. "
    "Wenn du genug weisst, antworte direkt in normalem Text nach den Regeln oben - beschreibe nicht den Tool-Aufruf. "
    "Wenn ein Tool fehlschlägt, sag ehrlich, dass es nicht geklappt hat; behaupte nie, etwas sei erledigt, "
    "wenn das Tool-Ergebnis das nicht bestätigt.\n"
    + UNTRUSTED_RULE
)


def time_context() -> str:
    now = local_now()
    return f"[Kontext: Jetzt ist {german_date(now)}, {now:%H:%M} Uhr.]"


@dataclass
class ToolCallLogEntry:
    name: str
    arguments: Dict[str, Any]
    success: bool
    data: Any = None
    error: Optional[str] = None


@dataclass
class PendingToolCall:
    """Returned instead of a reply when a call needs the user's approval.
    `arguments`/`summary`/`risk` are for the approval card; the spoken
    prompt (`reply`) deliberately does not read argument values aloud."""

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


def _tool_message(tool_call_id: str, name: str, result: ToolResult) -> Dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": tool_result_message_content(name, result.success, result.data, result.error, result.untrusted),
    }


class NativeToolCallingAgent:
    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        tool_names: Optional[List[str]] = None,
        provider_name: str = "local",
        source: str = "text",
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.tool_names = tool_names or exposed_tool_names(registry=tools)
        self.executor = ToolExecutor(tools)
        self.provider_name = provider_name
        self.source = source
        self.max_steps = get_settings().AGENT_MAX_STEPS

    def _ctx(self, db: Session | None, user_message: str, tainted: bool = False) -> ToolContext:
        return ToolContext(db=db, provider=self.provider_name, source=self.source, user_message=user_message, tainted=tainted)

    async def run(self, db: Session | None, user_message: str) -> ToolCallingResult:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            # Date/time travels with the user turn (not the system prompt) so
            # the system prompt stays byte-identical and Ollama's prompt cache hits.
            {"role": "user", "content": f"{time_context()}\n{user_message}"},
        ]
        ctx = self._ctx(db, user_message)
        return await self._loop(ctx, messages, call_log=[], start_step=1)

    async def resume(self, db: Session | None, approval: PendingApproval, approved: bool) -> ToolCallingResult:
        """Continue after the user approved or rejected `approval`."""
        ctx = self._ctx(db, approval.user_message, approval.tainted)
        plan = self.executor.plan(approval.tool_name, approval.arguments, ctx)
        if not approved:
            logger.info("TOOL CALL REJECTED BY USER -> %s", approval.tool_name)
            await self.executor.record_decision(plan, "rejected", ctx, approval_id=approval.approval_id)
            approval.call_log.append(ToolCallLogEntry(approval.tool_name, approval.arguments, False, None, "Vom Nutzer abgelehnt."))
            return ToolCallingResult(reply=REJECTED_REPLY, tool_calls=approval.call_log, steps_used=approval.steps_used)

        result = await self.executor.execute(approval.tool_name, approval.arguments, db=db, ctx=ctx, approval_id=approval.approval_id)
        if result.untrusted:
            ctx.tainted = True
        messages = approval.messages
        call_log = approval.call_log
        call_log.append(ToolCallLogEntry(approval.tool_name, approval.arguments, result.success, result.data, result.error))
        messages.append(_tool_message(approval.tool_call_id, approval.tool_name, result))

        if approval.remaining_calls:
            pending = await self._process_tool_calls(ctx, messages, call_log, approval.remaining_calls, approval.steps_used)
            if pending is not None:
                return pending
        return await self._loop(ctx, messages, call_log, start_step=approval.steps_used + 1)

    async def _loop(self, ctx: ToolContext, messages: List[Dict[str, Any]], call_log: List[ToolCallLogEntry], start_step: int) -> ToolCallingResult:
        tools_schema = to_openai_tools(self.tools, self.tool_names)
        for step in range(start_step, self.max_steps + 1):
            response = await self.llm.chat_with_tools(messages, tools=tools_schema)
            message = response.message
            messages.append(message)
            if ctx.db is not None:
                record_token_usage(ctx.db, self.provider_name, self.llm.model, response.usage)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return ToolCallingResult(reply=message.get("content") or "", tool_calls=call_log, steps_used=step)

            pending = await self._process_tool_calls(ctx, messages, call_log, tool_calls, step)
            if pending is not None:
                return pending

        logger.warning("NativeToolCallingAgent exceeded %d steps without a final answer.", self.max_steps)
        return ToolCallingResult(
            reply="Ich konnte die Anfrage nicht innerhalb des Schritt-Limits abschließen.",
            tool_calls=call_log,
            steps_used=self.max_steps,
        )

    async def _process_tool_calls(
        self,
        ctx: ToolContext,
        messages: List[Dict[str, Any]],
        call_log: List[ToolCallLogEntry],
        calls: List[Dict[str, Any]],
        step: int,
    ) -> Optional[ToolCallingResult]:
        """Handle `calls` in order. Pauses at the first call that needs
        approval; later calls of the same model turn are stored with the
        approval, so every tool_call_id eventually gets a tool message."""
        for i, call in enumerate(calls):
            function = call.get("function", {})
            name = function.get("name", "")
            arguments, raw_invalid = parse_tool_arguments(function.get("arguments", "{}"))
            call_id = call.get("id", "")
            if raw_invalid is not None:
                result = ToolResult(success=False, error=f"Arguments for '{name}' were not valid JSON - call the tool again with a JSON object.")
            else:
                plan = self.executor.plan(name, arguments, ctx)
                if plan.needs_approval:
                    approval_id = pending_approvals.create(
                        tool_name=name,
                        arguments=plan.arguments,
                        tool_call_id=call_id,
                        messages=messages,
                        call_log=call_log,
                        steps_used=step,
                        remaining_calls=calls[i + 1 :],
                        risk=plan.risk.value if plan.risk else "write",
                        summary=plan.summary,
                        provider=self.provider_name,
                        source=self.source,
                        user_message=ctx.user_message,
                        tainted=ctx.tainted,
                    )
                    await self.executor.record_decision(plan, "approval_requested", ctx, approval_id=approval_id)
                    logger.info("TOOL CALL PENDING APPROVAL -> %s approval_id=%s", name, approval_id)
                    return ToolCallingResult(
                        reply="Dafür brauche ich deine Bestätigung. Soll ich das machen?",
                        tool_calls=list(call_log),
                        steps_used=step,
                        pending_confirmation=PendingToolCall(
                            approval_id=approval_id,
                            name=name,
                            arguments=plan.arguments,
                            summary=plan.summary,
                            risk=plan.risk.value if plan.risk else "write",
                            expires_in_seconds=pending_approvals.ttl_seconds(),
                        ),
                    )
                # Unknown tool, invalid arguments, blocked, tainted refusal or
                # a normal run - the executor handles and audits each case.
                result = await self.executor.execute(name, arguments, db=ctx.db, ctx=ctx)
            if result.untrusted:
                ctx.tainted = True
            call_log.append(ToolCallLogEntry(name=name, arguments=arguments, success=result.success, data=result.data, error=result.error))
            messages.append(_tool_message(call_id, name, result))
        return None
