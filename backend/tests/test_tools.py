"""
Tool security regression tests.

delete_task is destructive and must never be executed automatically by
the agent (project spec section 14) - only a human, authenticated via
the REST endpoint (see test_tasks.py::test_delete_task), may delete a
task in V1. These tests guard against a future change accidentally
re-marking a destructive tool as SAFE.
"""
from __future__ import annotations

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


async def test_executor_refuses_delete_task_without_approval():
    """Even if the LLM decides to call delete_task, the single execution
    path (ToolExecutor) must not run it without the user's approval."""
    from app.tools.executor import ToolExecutor

    result = await ToolExecutor(build_default_registry()).execute("delete_task", {"task_id": 1})
    assert result.success is False and "approval" in result.error
