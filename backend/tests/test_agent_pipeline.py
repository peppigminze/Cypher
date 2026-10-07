"""
Unit tests for the multi-step Agent Core pipeline (app/agent/core.py),
using a scripted fake LLM so behavior is deterministic and doesn't
require a real Ollama instance.
"""
from __future__ import annotations

import json

import pytest

from app.agent.core import MAX_AGENT_STEPS, CypherAgent
from app.database.db import Base, SessionLocal, engine
from app.llm.base import ChatMessage, LLMProvider, LLMUnavailableError
from app.tools.defaults import build_default_registry


class ScriptedLLM(LLMProvider):
    """Returns each entry in `responses` in order, one per .chat() call."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[ChatMessage]] = []

    async def chat(self, messages, temperature: float = 0.3) -> str:
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("ScriptedLLM ran out of scripted responses.")
        return self.responses.pop(0)

    async def health_check(self) -> bool:
        return True


class AlwaysUnavailableLLM(LLMProvider):
    async def chat(self, messages, temperature: float = 0.3) -> str:
        raise LLMUnavailableError("down")

    async def health_check(self) -> bool:
        return False


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def plan_json(tool=None, arguments=None, done=True, reply="") -> str:
    return json.dumps({"tool": tool, "arguments": arguments or {}, "done": done, "reply": reply})


async def test_pipeline_answers_directly_without_a_tool(db_session):
    llm = ScriptedLLM([plan_json(tool=None, done=True, reply="Hallo!")])
    agent = CypherAgent(llm=llm, tools=build_default_registry())

    result = await agent.run_pipeline(db_session, "Hi Cypher")

    assert result.done is True
    assert result.reply == "Hallo!"
    assert result.observations == []


async def test_pipeline_runs_a_safe_tool_then_finishes(db_session):
    llm = ScriptedLLM(
        [
            plan_json(tool="get_current_time", done=True, reply="checking time"),
            "Es ist gerade Mittag.",  # the _summarize() call after the tool ran
        ]
    )
    agent = CypherAgent(llm=llm, tools=build_default_registry())

    result = await agent.run_pipeline(db_session, "Wie spät ist es?")

    assert result.done is True
    assert result.reply == "Es ist gerade Mittag."
    assert len(result.observations) == 1
    assert result.observations[0]["tool"] == "get_current_time"
    assert "utc_iso" in result.observations[0]["result"]


async def test_pipeline_chains_two_tool_calls(db_session):
    llm = ScriptedLLM(
        [
            plan_json(tool="create_task", arguments={"title": "Test"}, done=False, reply="creating"),
            plan_json(tool="list_tasks", done=True, reply="listing"),
            "Ich habe die Aufgabe erstellt.",
        ]
    )
    agent = CypherAgent(llm=llm, tools=build_default_registry())

    result = await agent.run_pipeline(db_session, "Leg eine Aufgabe an und zeig mir alle.")

    assert result.done is True
    assert [o["tool"] for o in result.observations] == ["create_task", "list_tasks"]


async def test_pipeline_pauses_on_confirm_required_tool(db_session):
    llm = ScriptedLLM(
        [plan_json(tool="delete_task", arguments={"task_id": 1}, done=False, reply="Soll ich Task 1 löschen?")]
    )
    agent = CypherAgent(llm=llm, tools=build_default_registry())

    result = await agent.run_pipeline(db_session, "Lösch Task 1")

    assert result.done is False
    assert result.pending_tool == "delete_task"
    assert result.pending_arguments == {"task_id": 1}
    assert result.observations == []


async def test_pipeline_resumes_after_confirmation(db_session):
    """Simulates what sync_worker does after a human confirms: execute
    the previously-pending tool, then resume with its observation."""
    from app.tools.base import ToolResult

    agent = CypherAgent(
        llm=ScriptedLLM([plan_json(tool=None, done=True, reply="ok"), "Task 1 wurde gelöscht."]),
        tools=build_default_registry(),
    )
    tool = agent.tools.get("delete_task")

    # First, create a real task so the delete has something to act on.
    create_tool = agent.tools.get("create_task")
    created = await create_tool.execute(db=db_session, title="Wegwerfen")
    task_id = created.data["id"]

    # The human confirmed in the PWA -> the sync worker passes the action id as approval.
    tool_result = await agent.execute_tools(db_session, tool, {"task_id": task_id}, approval_id="legacy-action-1")
    assert tool_result.success is True

    observations = [agent.build_observation("delete_task", {"task_id": task_id}, tool_result)]
    result = await agent.run_pipeline(db_session, "Lösch Task", observations=observations)

    assert result.done is True
    assert result.reply == "Task 1 wurde gelöscht."


async def test_pipeline_does_not_re_pause_on_a_repeated_failed_confirm_required_call(db_session):
    """Regression test for a bug found live: a write_file call rejected
    by the ALLOWED_DIRECTORIES sandbox got retried unchanged by
    llama3.1:8b, creating a second, identical PendingAction and leaving
    the message stuck in 'processing' forever since nothing ever
    resolved it. Retrying an identical call that already failed must
    stop the pipeline instead of pausing again on the same dead end."""
    llm = ScriptedLLM(
        [
            # Model retries the exact same (already-failed) call instead
            # of giving up or trying something different:
            plan_json(
                tool="write_file",
                arguments={"path": "C:\\Windows\\evil.txt", "content": "x"},
                done=False,
                reply="retrying",
            ),
            "Das konnte ich leider nicht ausführen.",
        ]
    )
    agent = CypherAgent(llm=llm, tools=build_default_registry())

    # Simulate: first attempt paused (write_file is CONFIRM_REQUIRED), the
    # human confirmed it, the agent executed it, and it failed (outside
    # ALLOWED_DIRECTORIES) - resuming with that failed observation.
    from app.tools.base import ToolResult

    failed_result = ToolResult(success=False, error="Path is outside the allowed directories.")
    observations = [
        agent.build_observation("write_file", {"path": "C:\\Windows\\evil.txt", "content": "x"}, failed_result)
    ]

    result = await agent.run_pipeline(db_session, "Write a file to C:\\Windows", observations=observations)

    assert result.done is True
    assert result.pending_tool is None
    assert len(result.observations) == 1  # the repeat was never re-attempted or re-paused on


async def test_pipeline_stops_at_max_steps_instead_of_looping_forever(db_session):
    # Always asks for another (safe) tool call with different arguments
    # each time (so the anti-duplication guard doesn't short-circuit it),
    # and never sets done=True.
    responses = [
        plan_json(tool="search_memory", arguments={"query": f"topic-{i}"}, done=False, reply="again")
        for i in range(MAX_AGENT_STEPS)
    ]
    responses.append("Ich konnte nicht alles abschließen.")
    llm = ScriptedLLM(responses)
    agent = CypherAgent(llm=llm, tools=build_default_registry())

    result = await agent.run_pipeline(db_session, "Mach unendlich viele Dinge")

    assert result.done is True
    assert len(result.observations) == MAX_AGENT_STEPS


async def test_pipeline_does_not_re_execute_a_repeated_identical_tool_call(db_session):
    """Regression test: llama3.1:8b in practice sometimes keeps calling
    the same tool with done=false even after it already succeeded. That
    must not create duplicate side effects (e.g. the same task twice)."""
    llm = ScriptedLLM(
        [
            plan_json(tool="create_task", arguments={"title": "Einmalig"}, done=False, reply="creating"),
            # Model repeats itself instead of setting done=true:
            plan_json(tool="create_task", arguments={"title": "Einmalig"}, done=False, reply="creating again"),
            "Ich habe die Aufgabe erstellt.",
        ]
    )
    agent = CypherAgent(llm=llm, tools=build_default_registry())

    result = await agent.run_pipeline(db_session, "Leg einmal die Aufgabe 'Einmalig' an.")

    assert result.done is True
    assert len(result.observations) == 1  # not executed twice

    from app.database.models import Task

    assert db_session.query(Task).count() == 1


async def test_llm_unavailable_propagates(db_session):
    agent = CypherAgent(llm=AlwaysUnavailableLLM(), tools=build_default_registry())
    with pytest.raises(LLMUnavailableError):
        await agent.run_pipeline(db_session, "Hallo")
