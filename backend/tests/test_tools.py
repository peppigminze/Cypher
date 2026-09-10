"""
Tool security regression tests.

delete_task is destructive and must never be executed automatically by
the agent (project spec section 14) - only a human, authenticated via
the REST endpoint (see test_tasks.py::test_delete_task), may delete a
task in V1. These tests guard against a future change accidentally
re-marking a destructive tool as SAFE.
"""
from __future__ import annotations

from app.agent.core import CypherAgent, PlanDecision
from app.tools.base import ToolSecurity
from app.tools.defaults import build_default_registry


def test_delete_task_requires_confirmation():
    registry = build_default_registry()
    assert registry.get("delete_task").security == ToolSecurity.CONFIRM_REQUIRED
    assert registry.is_executable_automatically("delete_task") is False


def test_create_and_list_and_complete_task_remain_safe():
    registry = build_default_registry()
    for name in ("create_task", "list_tasks", "complete_task"):
        assert registry.get(name).security == ToolSecurity.SAFE
        assert registry.is_executable_automatically(name) is True


def test_agent_skips_confirm_required_tool_selected_by_the_model():
    """Even if the LLM decides to call delete_task, the agent must not
    hand it to execute_tools - see CypherAgent.select_tools."""
    agent = CypherAgent(llm=None, tools=build_default_registry())  # type: ignore[arg-type]
    decision = PlanDecision(tool="delete_task", arguments={"task_id": 1}, reply="Lösche Task 1.")
    assert agent.select_tools(decision) is None
