"""Core-path coverage: ToolExecutor (the single execution path) and the
approval gate of the native tool-calling agent, driven by a scripted LLM."""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from app.agent.native_tool_agent import NativeToolCallingAgent
from tests.llm_doubles import ChatWithToolsResponse, OneShotLLM
from app.tools import pending_approvals
from app.tools.base import Tool, ToolResult, ToolSecurity
from app.tools.registry import ToolRegistry
from app.tools.tool_executor import ToolExecutor, to_openai_tools


class RecordingTool(Tool):
    def __init__(self, name: str, security: ToolSecurity, fail: bool = False) -> None:
        self.name = name
        self.description = f"{name} test tool"
        self.parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
        self.security = security
        self.calls: List[Dict[str, Any]] = []
        self.fail = fail

    async def execute(self, db=None, **kwargs) -> ToolResult:
        if self.fail:
            raise RuntimeError("kaputt")
        self.calls.append(kwargs)
        return ToolResult(success=True, data={"echo": kwargs})


class ScriptedLLM(OneShotLLM):
    """Returns pre-baked assistant messages in order."""

    name = "local"
    model = "scripted"

    def __init__(self, turns: List[Dict[str, Any]]) -> None:
        self.turns = list(turns)
        self.seen: List[List[Dict[str, Any]]] = []

    async def chat(self, messages, temperature=0.3) -> str:
        return ""

    async def chat_with_tools(self, messages, tools, temperature=0.2) -> ChatWithToolsResponse:
        self.seen.append([dict(m) for m in messages])
        return ChatWithToolsResponse(message=self.turns.pop(0), usage=None)

    async def health_check(self) -> bool:
        return True


def tool_call(name: str, args: dict, call_id: str = "call_1") -> Dict[str, Any]:
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def final(text: str) -> Dict[str, Any]:
    return {"role": "assistant", "content": text}


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    reg.register(RecordingTool("safe_tool", ToolSecurity.SAFE))
    reg.register(RecordingTool("gated_tool", ToolSecurity.CONFIRM_REQUIRED))
    reg.register(RecordingTool("blocked_tool", ToolSecurity.BLOCKED))
    reg.register(RecordingTool("broken_tool", ToolSecurity.SAFE, fail=True))
    return reg


# ------------------------------------------------------------------ ToolExecutor
async def test_executor_runs_tool_and_never_raises(registry):
    executor = ToolExecutor(registry)
    ok = await executor.execute("safe_tool", {"text": "hi"})
    assert ok.success and registry.get("safe_tool").calls == [{"text": "hi"}]

    unknown = await executor.execute("nope", {})
    assert not unknown.success and "Unknown tool" in unknown.error

    crashed = await executor.execute("broken_tool", {})
    assert not crashed.success and "kaputt" in crashed.error


def test_parse_arguments_is_defensive():
    assert ToolExecutor.parse_arguments('{"a": 1}') == {"a": 1}
    assert ToolExecutor.parse_arguments("") == {}
    assert ToolExecutor.parse_arguments("{broken") == {}
    assert ToolExecutor.parse_arguments("[1, 2]") == {}


def test_openai_schema_skips_unknown_names(registry):
    schemas = to_openai_tools(registry, ["safe_tool", "does_not_exist"])
    assert [s["function"]["name"] for s in schemas] == ["safe_tool"]


# ------------------------------------------------------------------ approval gate
def agent_for(registry, turns) -> NativeToolCallingAgent:
    return NativeToolCallingAgent(llm=ScriptedLLM(turns), tools=registry, tool_names=["safe_tool", "gated_tool", "blocked_tool"])


async def test_safe_tool_runs_without_confirmation(registry):
    agent = agent_for(registry, [tool_call("safe_tool", {"text": "x"}), final("Fertig.")])
    result = await agent.run(None, "mach")
    assert result.reply == "Fertig." and result.pending_confirmation is None
    assert registry.get("safe_tool").calls == [{"text": "x"}]


async def test_gated_tool_pauses_and_does_not_execute(registry):
    agent = agent_for(registry, [tool_call("gated_tool", {"text": "notiz"})])
    result = await agent.run(None, "schreib")
    assert result.pending_confirmation is not None
    assert result.pending_confirmation.name == "gated_tool"
    assert registry.get("gated_tool").calls == []


async def test_confirmed_tool_runs_exactly_once(registry):
    agent = agent_for(registry, [tool_call("gated_tool", {"text": "notiz"}), final("Gespeichert.")])
    paused = await agent.run(None, "schreib")
    approval = pending_approvals.pop(paused.pending_confirmation.approval_id)
    resumed = await agent.resume(None, approval, approved=True)
    assert resumed.reply == "Gespeichert."
    assert registry.get("gated_tool").calls == [{"text": "notiz"}]
    # Single use: a double-click on "Ja" cannot run it twice.
    assert pending_approvals.pop(paused.pending_confirmation.approval_id) is None


async def test_rejected_tool_never_runs(registry):
    agent = agent_for(registry, [tool_call("gated_tool", {"text": "notiz"}), final("Okay, nicht gemacht.")])
    paused = await agent.run(None, "schreib")
    approval = pending_approvals.pop(paused.pending_confirmation.approval_id)
    await agent.resume(None, approval, approved=False)
    assert registry.get("gated_tool").calls == []


async def test_blocked_tool_is_refused_without_confirmation(registry):
    agent = agent_for(registry, [tool_call("blocked_tool", {}), final("Geht nicht.")])
    result = await agent.run(None, "mach")
    assert result.pending_confirmation is None
    assert registry.get("blocked_tool").calls == []
    assert result.tool_calls[0].success is False


async def test_expired_approval_cannot_be_used(registry, monkeypatch):
    agent = agent_for(registry, [tool_call("gated_tool", {"text": "x"})])
    paused = await agent.run(None, "schreib")
    monkeypatch.setattr(pending_approvals, "PENDING_TTL_SECONDS", -1)
    assert pending_approvals.pop(paused.pending_confirmation.approval_id) is None


async def test_step_limit_stops_tool_loops(registry):
    agent = agent_for(registry, [tool_call("safe_tool", {"text": str(i)}, f"c{i}") for i in range(10)])
    result = await agent.run(None, "loop")
    assert "Schritt-Limit" in result.reply
    assert len(registry.get("safe_tool").calls) == 5
