"""
The single authorized execution path for tools.

Every tool call - from the voice agent, text chat, tests - goes through
`ToolExecutor.execute()`, which, in this order:

  1. looks the tool up (unknown name -> error the LLM can recover from)
  2. validates arguments with the tool's pydantic model
  3. computes the per-call risk tier
  4. refuses BLOCKED tools
  5. refuses write/destructive tools unless the caller passes an approval
     (defence in depth: even a buggy caller cannot skip the approval gate)
  6. refuses taint-sensitive tools once untrusted content entered the turn
  7. runs the tool with a timeout; exceptions become a failed ToolResult
  8. marks external content as untrusted
  9. writes the audit log and the latency metric

The LLM only ever *proposes* a call; this class decides whether it runs.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.metrics import metrics
from app.tools.audit import AuditLog, AuditRecord, get_audit_log
from app.tools.base import RiskTier, Tool, ToolArgumentError, ToolResult
from app.tools.context import ToolContext
from app.tools.registry import ToolRegistry

logger = logging.getLogger("cypher.tools.executor")

DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0


@dataclass
class PlannedCall:
    """Outcome of checking a proposed call without running it."""

    name: str
    tool: Optional[Tool]
    arguments: Dict[str, Any]
    risk: Optional[RiskTier] = None
    summary: str = ""
    error: Optional[str] = None
    error_kind: Optional[str] = None  # "unknown_tool" | "invalid"

    @property
    def needs_approval(self) -> bool:
        return self.error is None and self.risk is not None and self.risk.needs_approval


def to_openai_tools(registry: ToolRegistry, names: List[str]) -> List[Dict[str, Any]]:
    """OpenAI/Ollama `tools` array for the given names (unknown names skipped)."""
    schemas: List[Dict[str, Any]] = []
    for name in names:
        tool = registry.get(name)
        if tool is None:
            logger.warning("to_openai_tools: no such tool '%s' - skipped", name)
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                },
            }
        )
    return schemas


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, audit: Optional[AuditLog] = None, default_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS) -> None:
        self.registry = registry
        self._audit = audit
        self.default_timeout = default_timeout

    @property
    def audit(self) -> AuditLog:
        return self._audit or get_audit_log()

    # ------------------------------------------------------------ planning
    def plan(self, name: str, raw_arguments: Dict[str, Any], ctx: Optional[ToolContext] = None) -> PlannedCall:
        tool = self.registry.get(name)
        if tool is None:
            return PlannedCall(name, None, raw_arguments or {}, error=f"Unknown tool '{name}'.", error_kind="unknown_tool")
        try:
            args = tool.validate_args(raw_arguments or {})
        except ToolArgumentError as exc:
            return PlannedCall(name, tool, raw_arguments or {}, error=str(exc), error_kind="invalid")
        risk = tool.risk_for(args, ctx)
        return PlannedCall(name, tool, args, risk=risk, summary=tool.describe(args))

    # ------------------------------------------------------------ execution
    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        db: Any = None,
        ctx: Optional[ToolContext] = None,
        *,
        approval_id: Optional[str] = None,
    ) -> ToolResult:
        ctx = ctx or ToolContext(db=db)
        if ctx.db is None and db is not None:
            ctx.db = db
        plan = self.plan(name, arguments, ctx)
        base = AuditRecord(
            tool=name,
            decision="auto",
            arguments=plan.arguments,
            summary=plan.summary or None,
            risk=plan.risk.value if plan.risk else None,
            approval_id=approval_id,
            conversation_id=ctx.conversation_id,
            source=ctx.source,
            provider=ctx.provider,
        )

        if plan.error:
            await self._audit_write(base, decision=plan.error_kind or "invalid", success=False, error=plan.error)
            return ToolResult(success=False, error=plan.error)

        tool = plan.tool
        assert tool is not None and plan.risk is not None
        if plan.risk == RiskTier.BLOCKED:
            error = f"Tool '{name}' is blocked and cannot be run."
            await self._audit_write(base, decision="blocked", success=False, error=error)
            return ToolResult(success=False, error=error)
        # Taint first: no approval can unlock these, so say the real reason.
        if ctx.tainted and not tool.allow_when_tainted:
            error = (
                f"Tool '{name}' was not run: this turn contains untrusted external content, "
                "and this action may only follow a direct request from the user."
            )
            await self._audit_write(base, decision="tainted_blocked", success=False, error=error)
            return ToolResult(success=False, error=error)
        if plan.risk.needs_approval and not approval_id:
            error = f"Tool '{name}' needs the user's approval before it can run."
            await self._audit_write(base, decision="needs_approval", success=False, error=error)
            return ToolResult(success=False, error=error)

        started = time.perf_counter()
        result = await self._run(tool, plan.arguments, ctx)
        duration = time.perf_counter() - started
        metrics.record("tool", duration)
        if tool.untrusted_output:
            result.untrusted = True

        await self._audit_write(
            base,
            decision="approved" if approval_id else "auto",
            success=result.success,
            error=result.error,
            duration_ms=int(duration * 1000),
            result=result.data,
        )
        logger.info("TOOL %s -> success=%s (%.0f ms)%s", name, result.success, duration * 1000, f" error={result.error}" if result.error else "")
        return result

    async def _run(self, tool: Tool, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        timeout = tool.timeout_seconds or self.default_timeout
        kwargs = dict(args)
        if _accepts(tool.execute, "ctx"):
            kwargs["ctx"] = ctx
        try:
            result = await asyncio.wait_for(tool.execute(db=ctx.db, **kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Tool '{tool.name}' timed out after {timeout:.0f}s.")
        except TypeError as exc:
            return ToolResult(success=False, error=f"Invalid arguments for tool '{tool.name}': {exc}")
        except Exception as exc:  # noqa: BLE001 - a tool must never crash the conversation
            logger.exception("Tool '%s' raised an unexpected error", tool.name)
            return ToolResult(success=False, error=f"Tool '{tool.name}' failed: {exc}")
        if not isinstance(result, ToolResult):
            return ToolResult(success=False, error=f"Tool '{tool.name}' returned an invalid result.")
        return result

    async def record_decision(self, plan: PlannedCall, decision: str, ctx: Optional[ToolContext] = None, approval_id: Optional[str] = None, error: Optional[str] = None) -> None:
        """Audit entries for approval lifecycle events (requested, rejected, expired)."""
        ctx = ctx or ToolContext()
        await self._audit_write(
            AuditRecord(
                tool=plan.name,
                decision=decision,
                arguments=plan.arguments,
                summary=plan.summary or None,
                risk=plan.risk.value if plan.risk else None,
                approval_id=approval_id,
                conversation_id=ctx.conversation_id,
                source=ctx.source,
                provider=ctx.provider,
            ),
            decision=decision,
            error=error,
        )

    async def _audit_write(self, base: AuditRecord, *, decision: str, success: Optional[bool] = None, error: Optional[str] = None, duration_ms: Optional[int] = None, result: Any = None) -> None:
        base.decision = decision
        base.success = success
        base.error = error
        base.duration_ms = duration_ms
        base.result = result
        await self.audit.write(base)

    @staticmethod
    def parse_arguments(raw_arguments: Any) -> Dict[str, Any]:
        """Tool-call arguments arrive as a JSON string (OpenAI) or object (Ollama)."""
        from app.llm.base import parse_tool_arguments  # noqa: PLC0415

        args, _raw = parse_tool_arguments(raw_arguments)
        return args


def _accepts(fn: Any, param: str) -> bool:
    """Only tools that explicitly declare `ctx` receive it (a catch-all
    **kwargs does not count - most tools neither need nor expect it)."""
    try:
        return param in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
