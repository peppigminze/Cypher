"""ConversationEngine (app/agent/engine.py) driven by a scripted streaming LLM:
streaming sentences, tool calls, approvals, barge-in, loop protection,
quick commands and the "claimed but not done" guard."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from app.agent import engine as engine_mod
from app.agent.action_guard import NOT_DONE_REPLY, RETRY_HINT
from app.agent.engine import (
    INTERRUPTED_MARK,
    REJECTED_REPLY,
    STEP_LIMIT_REPLY,
    ApprovalRequired,
    Conversation,
    ConversationEngine,
    ConversationStore,
    Sentence,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnDone,
    TurnFailed,
)
from app.llm.base import LLMProvider, LLMUnavailableError, StreamDone, ToolCall, ToolCallEvent
from app.llm.base import TextDelta as LLMText
from app.tools import pending_approvals
from app.tools.base import RiskTier, Tool, ToolResult
from app.tools.registry import ToolRegistry


# ------------------------------------------------------------------ doubles
class ScriptedLLM(LLMProvider):
    """Each step is a list of text chunks and ToolCall objects, streamed in order."""

    name = "local"
    model = "scripted"

    def __init__(self, steps: List[List[Any]]) -> None:
        self.steps = list(steps)
        self.calls: List[List[Dict[str, Any]]] = []
        self.finished_steps = 0

    async def chat(self, messages, temperature=0.3):  # pragma: no cover - unused
        return ""

    async def health_check(self):  # pragma: no cover - unused
        return True

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        if not self.steps:
            raise AssertionError("LLM called more often than scripted")
        for item in self.steps.pop(0):
            if isinstance(item, Exception):
                raise item
            if isinstance(item, ToolCall):
                yield ToolCallEvent(item)
            else:
                yield LLMText(item)
        self.finished_steps += 1
        yield StreamDone()


def call(name: str, args: Optional[Dict[str, Any]] = None, call_id: str = "c1", raw: Optional[str] = None) -> ToolCall:
    return ToolCall(call_id, name, args or {}, raw)


class EchoTool(Tool):
    name = "echo"
    description = "echo"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    risk = RiskTier.READ_ONLY
    spoken_progress = "Ich schaue nach."

    def __init__(self):
        self.runs: List[Dict[str, Any]] = []

    async def execute(self, db=None, **kwargs):
        self.runs.append(kwargs)
        return ToolResult(success=True, data={"echo": kwargs.get("text", "")})


class NoteTool(EchoTool):
    name = "save_note"
    risk = RiskTier.WRITE
    spoken_progress = None

    def describe(self, args):
        return f"Notiz speichern: „{args.get('text', '')}“"


class MediaTool(EchoTool):
    name = "media_control"
    parameters = {"type": "object", "properties": {"action": {"type": "string"}}}
    risk = RiskTier.CONTROL
    spoken_progress = None


@pytest.fixture()
def tools():
    reg = ToolRegistry()
    for tool in (EchoTool(), NoteTool(), MediaTool()):
        reg.register(tool)
    return reg


def make_engine(llm, registry, store=None):
    return ConversationEngine(llm, registry, store=store or ConversationStore(), tool_names=["echo", "save_note", "media_control"])


async def run(engine, text, cid="conv", source="voice"):
    return [e async for e in engine.run_turn(cid, text, source=source)]


@pytest.fixture(autouse=True)
def _clean_approvals():
    pending_approvals.clear()
    yield
    pending_approvals.clear()


# ------------------------------------------------------------------ streaming
async def test_sentences_stream_before_the_model_is_done(tools):
    llm = ScriptedLLM([["Es ist ", "zwölf Uhr. ", "Noch ", "etwas?"]])
    engine = make_engine(llm, tools)
    events = []
    async for event in engine.run_turn("c", "Wie spät ist es?"):
        if isinstance(event, Sentence) and event.index == 0:
            # The first sentence arrives while the LLM is still streaming.
            assert llm.finished_steps == 0
        events.append(event)
    assert [e.text for e in events if isinstance(e, Sentence)] == ["Es ist zwölf Uhr.", "Noch etwas?"]
    done = events[-1]
    assert isinstance(done, TurnDone) and done.reply == "Es ist zwölf Uhr. Noch etwas?"
    assert "first_sentence" in done.metrics and done.metrics["llm_calls"] == 1


async def test_system_prompt_is_constant_and_time_travels_with_the_user_turn(tools):
    llm = ScriptedLLM([["A."], ["B."]])
    engine = make_engine(llm, tools)
    await run(engine, "Eins")
    await run(engine, "Zwei")
    first, second = llm.calls
    assert first[0] == second[0]  # byte-identical system prompt -> prompt cache hits
    assert first[-1]["content"].startswith("[Kontext: Jetzt ist ") and first[-1]["content"].endswith("Eins")
    # History of turn one is part of turn two.
    assert [m["role"] for m in second[1:]] == ["user", "assistant", "user"]


# ------------------------------------------------------------------ tools
async def test_tool_call_then_answer_with_spoken_progress(tools):
    llm = ScriptedLLM([[call("echo", {"text": "hi"})], ["Ergebnis: hi."]])
    events = await run(make_engine(llm, tools), "Schau mal nach hi")
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("ToolStarted") < kinds.index("ToolFinished")
    sentences = [e for e in events if isinstance(e, Sentence)]
    assert sentences[0].text == "Ich schaue nach." and sentences[0].index == -1
    assert sentences[-1].text == "Ergebnis: hi."
    assert tools.get("echo").runs == [{"text": "hi"}]
    tool_msg = llm.calls[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"


async def test_progress_phrase_only_for_voice(tools):
    llm = ScriptedLLM([[call("echo", {"text": "x"})], ["Fertig."]])
    events = await run(make_engine(llm, tools), "Schau nach", source="text")
    assert [e.text for e in events if isinstance(e, Sentence)] == ["Fertig."]


async def test_invalid_json_arguments_are_sent_back_for_a_retry(tools):
    llm = ScriptedLLM([[call("echo", {}, raw="{text: kaputt")], [call("echo", {"text": "ok"}, call_id="c2")], ["Gut."]])
    events = await run(make_engine(llm, tools), "Echo bitte")
    assert "not valid JSON" in llm.calls[1][-1]["content"]
    assert tools.get("echo").runs == [{"text": "ok"}]
    assert events[-1].reply == "Gut."


async def test_identical_repeated_call_is_refused(tools):
    llm = ScriptedLLM([[call("echo", {"text": "a"})], [call("echo", {"text": "a"}, call_id="c2")], ["Fertig."]])
    await run(make_engine(llm, tools), "Echo a")
    assert len(tools.get("echo").runs) == 1
    assert "already called" in llm.calls[2][-1]["content"]


async def test_step_limit(tools, monkeypatch):
    steps = [[call("echo", {"text": str(i)}, call_id=f"c{i}")] for i in range(10)]
    llm = ScriptedLLM(steps)
    engine = make_engine(llm, tools)
    engine.settings = engine.settings.model_copy(update={"AGENT_MAX_STEPS": 3})
    events = await run(engine, "Endlos")
    assert events[-1].reply == STEP_LIMIT_REPLY and len(llm.calls) == 3


async def test_llm_unavailable_ends_the_turn_with_a_german_error(tools):
    llm = ScriptedLLM([[LLMUnavailableError("connection refused", provider="local", base_url="http://127.0.0.1:11434")]])
    events = await run(make_engine(llm, tools), "Hallo")
    assert isinstance(events[-1], TurnFailed)
    assert events[-1].error.code == "ollama_unreachable" and "Ollama" in events[-1].error.hint


# ------------------------------------------------------------------ approvals
async def test_write_tool_pauses_for_approval_and_resumes(tools):
    store = ConversationStore()
    llm = ScriptedLLM([[call("save_note", {"text": "Brot"})], ["Notiz gespeichert."]])
    engine = make_engine(llm, tools, store)
    events = await run(engine, "Notiere Brot", cid="a")
    approval = events[-1]
    assert isinstance(approval, ApprovalRequired)
    assert approval.spoken_prompt == "Notiz speichern: „Brot“. Soll ich das machen?"
    assert tools.get("save_note").runs == []  # nothing ran yet
    # Only the spoken question is in the history while waiting.
    assert store.get("a").messages[-1] == {"role": "assistant", "content": approval.spoken_prompt}

    pending = pending_approvals.pop(approval.approval_id)
    resumed = [e async for e in engine.resume(pending, approved=True)]
    assert tools.get("save_note").runs == [{"text": "Brot"}]
    assert isinstance(resumed[-1], TurnDone) and resumed[-1].reply == "Notiz gespeichert."
    # The history stays valid for the API: no dangling tool calls.
    assert all(not m.get("tool_calls") and m["role"] != "tool" for m in store.get("a").messages)


async def test_rejected_approval_does_not_run_the_tool(tools):
    llm = ScriptedLLM([[call("save_note", {"text": "Brot"})]])
    engine = make_engine(llm, tools)
    approval = (await run(engine, "Notiere Brot"))[-1]
    pending = pending_approvals.pop(approval.approval_id)
    resumed = [e async for e in engine.resume(pending, approved=False)]
    assert tools.get("save_note").runs == []
    assert resumed[-1].reply == REJECTED_REPLY


# ------------------------------------------------------------------ barge-in
async def test_interrupted_turn_keeps_what_was_said(tools):
    store = ConversationStore()
    llm = ScriptedLLM([["Erster Satz. ", "Zweiter ", "Satz kommt nie."]])
    engine = make_engine(llm, tools, store)
    gen = engine.run_turn("i", "Erzähl was")
    async for event in gen:
        if isinstance(event, Sentence):
            break  # user talks over Cypher -> the session cancels the turn
    await gen.aclose()
    history = store.get("i").messages
    assert history[-1] == {"role": "assistant", "content": "Erster Satz." + INTERRUPTED_MARK}


# ------------------------------------------------------------------ guard
async def test_claimed_action_without_tool_is_held_back_and_retried(tools):
    store = ConversationStore()
    llm = ScriptedLLM([["Notiz gespeichert: Brot."], [call("save_note", {"text": "Brot"})]])
    engine = make_engine(llm, tools, store)
    events = await run(engine, "Notiere: Brot", cid="g")
    # The false claim was never emitted (not spoken, not subtitled).
    assert not any(isinstance(e, (Sentence, TextDelta)) and "gespeichert" in e.text for e in events)
    assert isinstance(events[-1], ApprovalRequired)
    assert llm.calls[1][-1]["content"].endswith(RETRY_HINT)
    assert all(RETRY_HINT not in (m.get("content") or "") for m in store.get("g").messages)


async def test_claim_twice_gives_an_honest_answer(tools):
    llm = ScriptedLLM([["Timer läuft."], ["Erledigt."]])
    events = await run(make_engine(llm, tools), "Stell einen Timer auf zehn Minuten.")
    assert [e.text for e in events if isinstance(e, Sentence)] == [NOT_DONE_REPLY]
    assert events[-1].reply == NOT_DONE_REPLY and events[-1].metrics["guard_retries"] == 2


async def test_clarifying_question_passes_the_guard(tools):
    llm = ScriptedLLM([["Für wie lange?"]])
    events = await run(make_engine(llm, tools), "Stell einen Timer.")
    assert [e.text for e in events if isinstance(e, Sentence)] == ["Für wie lange?"]


async def test_questions_are_not_held_back(tools):
    llm = ScriptedLLM([["Ein Timer zählt runter. ", "Ein Wecker klingelt zu einer Uhrzeit."]])
    engine = make_engine(llm, tools)
    async for event in engine.run_turn("q", "Was ist der Unterschied zwischen Timer und Wecker?"):
        if isinstance(event, Sentence):
            assert llm.finished_steps == 0  # streamed, not buffered
            break


# ------------------------------------------------------------------ quick commands
async def test_quick_command_skips_the_llm(tools):
    store = ConversationStore()
    llm = ScriptedLLM([])
    events = await run(make_engine(llm, tools, store), "Pause.", cid="qc")
    assert llm.calls == []
    assert tools.get("media_control").runs == [{"action": "pause"}]
    assert [e.text for e in events if isinstance(e, Sentence)] == ["Pausiert."]
    assert events[-1].metrics["quick_command"] == 1.0
    assert store.get("qc").messages[-1] == {"role": "assistant", "content": "Pausiert."}


async def test_quick_command_not_used_when_tool_is_not_exposed(tools):
    llm = ScriptedLLM([["Okay."]])
    engine = ConversationEngine(llm, tools, store=ConversationStore(), tool_names=["echo"])
    await run(engine, "Pause.")
    assert len(llm.calls) == 1


# ------------------------------------------------------------------ history window
def test_history_window_cuts_only_at_user_messages():
    conv = Conversation("w")
    for i in range(30):
        conv.messages += [
            {"role": "user", "content": f"Frage {i} " + "x" * 200},
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"t{i}", "function": {"name": "echo", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"t{i}", "content": "y" * 200},
            {"role": "assistant", "content": f"Antwort {i}"},
        ]
    window = conv.window(500)
    assert window and window[0]["role"] == "user" and len(window) < len(conv.messages)
    assert window[-1]["content"] == "Antwort 29"


def test_long_tool_results_are_compacted_in_history():
    conv = Conversation("c")
    conv.messages.append({"role": "tool", "content": "z" * 5000})
    conv.compact_tool_results()
    assert len(conv.messages[0]["content"]) < 700 and conv.messages[0]["content"].endswith("[gekürzt]")


def test_spoken_approval_prompt_is_short():
    prompt = engine_mod.spoken_approval_prompt("x" * 400)
    assert prompt.endswith("Soll ich das machen?") and len(prompt) < 170
