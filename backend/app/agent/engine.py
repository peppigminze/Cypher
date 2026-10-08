"""
Conversation engine: ONE agent loop for voice, text chat and /api/tool-chat.

    run_turn(conversation_id, user_text)  ->  async stream of events
        TextDelta     raw model tokens (live subtitles)
        Sentence      a complete sentence, ready for TTS (streaming speech)
        ToolStarted / ToolFinished
        ApprovalRequired   turn pauses until resume(approval_id, ...)
        TurnDone      final reply + per-turn metrics
        TurnFailed    German error (catalog) - the turn ended without an answer

Design points:
  * Streaming end to end: sentences are emitted while the model is still
    generating, so speech starts after the first sentence instead of after
    the whole answer.
  * Cancellation: the caller runs a turn as an asyncio task and cancels it
    on barge-in. Cancelling closes the HTTP stream (Ollama stops
    generating) and the partial answer is still written to the history,
    marked as interrupted, so the next turn has correct context.
  * Tools only via app/tools/executor.py (validation, risk tiers, audit);
    write/destructive calls pause the turn for the user's approval.
  * Robustness: malformed tool arguments are sent back to the model once
    for a retry, an identical repeated call is refused (tool loops), and the
    number of LLM steps per turn is capped (AGENT_MAX_STEPS).
  * Date/time travels with the user message, so the system prompt stays
    byte-identical and Ollama's prompt cache keeps hitting.
  * Short media/volume commands ("Pause.", "Leiser.") skip the LLM
    (app/agent/quick_commands.py); "claimed but not done" answers are caught
    by app/agent/action_guard.py.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from app.agent.action_guard import NOT_DONE_REPLY, RETRY_HINT, claims_action, looks_like_action_request
from app.agent.quick_commands import QuickCommand, match_quick_command
from app.config import Settings, get_settings
from app.core import errors
from app.core.context import pipeline_id_var
from app.core.metrics import metrics
from app.llm.base import LLMProvider, LLMUnavailableError, StreamDone, TextDelta as LLMTextDelta, ToolCall, ToolCallEvent, stream_events
from app.persona import system_prompt
from app.security.untrusted import tool_result_message_content
from app.tools import pending_approvals
from app.tools.base import ToolResult
from app.tools.catalog import exposed_tool_names
from app.tools.context import ToolContext
from app.tools.executor import ToolExecutor, to_openai_tools
from app.tools.registry import ToolRegistry
from app.tools.system_tools import german_date, local_now
from app.voice.sentences import SentenceSplitter

logger = logging.getLogger("cypher.agent.engine")

REJECTED_REPLY = "Okay, ich habe es nicht ausgeführt."
STEP_LIMIT_REPLY = "Ich konnte die Anfrage nicht innerhalb des Schritt-Limits abschließen."
INTERRUPTED_MARK = " … [vom Nutzer unterbrochen]"
_INTERRUPTED_BEFORE_ANSWER = "[vom Nutzer unterbrochen, bevor ich antworten konnte]"
_HISTORY_TOOL_RESULT_CHARS = 600


# ------------------------------------------------------------------ events
@dataclass
class TextDelta:
    text: str


@dataclass
class Sentence:
    text: str
    index: int


@dataclass
class ToolStarted:
    name: str
    summary: str


@dataclass
class ToolFinished:
    name: str
    arguments: Dict[str, Any]
    success: bool
    summary: str
    data: Any = None
    error: Optional[str] = None


@dataclass
class ApprovalRequired:
    approval_id: str
    tool_name: str
    arguments: Dict[str, Any]
    summary: str
    risk: str
    expires_in_seconds: int
    spoken_prompt: str
    steps_used: int


@dataclass
class TurnDone:
    reply: str
    steps_used: int
    metrics: Dict[str, float] = field(default_factory=dict)


@dataclass
class TurnFailed:
    error: errors.CypherError


EngineEvent = Union[TextDelta, Sentence, ToolStarted, ToolFinished, ApprovalRequired, TurnDone, TurnFailed]


# ------------------------------------------------------------------ conversation memory
def estimate_tokens(text: str) -> int:
    # ~3.5 characters per token for German with Qwen's tokenizer - good
    # enough for budgeting, no tokenizer dependency.
    return int(len(text) / 3.5) + 4


def _message_tokens(msg: Dict[str, Any]) -> int:
    total = estimate_tokens(msg.get("content") or "")
    for call in msg.get("tool_calls") or []:
        total += estimate_tokens(json.dumps(call.get("function", {}), ensure_ascii=False))
    return total


@dataclass
class Conversation:
    id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def window(self, max_tokens: int) -> List[Dict[str, Any]]:
        """Most recent history within the token budget. Cuts only at user
        messages, so an assistant tool call is never separated from its
        tool results (the API would reject that)."""
        budget = max_tokens
        start = len(self.messages)
        for i in range(len(self.messages) - 1, -1, -1):
            budget -= _message_tokens(self.messages[i])
            if budget < 0:
                break
            if self.messages[i]["role"] == "user":
                start = i
        return self.messages[start:]

    def mark_interrupted(self, heard: str) -> None:
        """Barge-in while Cypher was speaking: the history should hold what
        the user actually heard, not the whole generated answer - otherwise
        the next turn assumes they know things they never heard."""
        if not self.messages:
            return
        last = self.messages[-1]
        if last["role"] != "assistant" or last.get("tool_calls"):
            return
        heard = heard.strip()
        self.messages[-1] = {"role": "assistant", "content": (heard + INTERRUPTED_MARK) if heard else _INTERRUPTED_BEFORE_ANSWER}

    def compact_tool_results(self) -> None:
        """Long tool results (web pages, files) are only needed in the turn
        that fetched them; keep a short version in the history."""
        for msg in self.messages:
            content = msg.get("content") or ""
            if msg["role"] == "tool" and len(content) > _HISTORY_TOOL_RESULT_CHARS:
                msg["content"] = content[:_HISTORY_TOOL_RESULT_CHARS] + " …[gekürzt]"


class ConversationStore:
    """In-memory conversations (M4 adds persistence + summaries)."""

    def __init__(self, max_conversations: int = 50) -> None:
        self._items: Dict[str, Conversation] = {}
        self._max = max_conversations

    def get(self, conversation_id: str) -> Conversation:
        conv = self._items.get(conversation_id)
        if conv is None:
            if len(self._items) >= self._max:
                oldest = min(self._items.values(), key=lambda c: c.updated_at)
                self._items.pop(oldest.id, None)
            conv = Conversation(conversation_id)
            self._items[conversation_id] = conv
        return conv

    def reset(self, conversation_id: str) -> None:
        self._items.pop(conversation_id, None)


conversations = ConversationStore()


def time_context() -> str:
    now = local_now()
    return f"[Kontext: Jetzt ist {german_date(now)}, {now:%H:%M} Uhr.]"


def _tool_message(call_id: str, name: str, result: ToolResult) -> Dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": tool_result_message_content(name, result.success, result.data, result.error, result.untrusted)}


def spoken_approval_prompt(summary: str) -> str:
    summary = summary.strip().rstrip(".")
    if len(summary) > 140:
        summary = summary[:137] + "…"
    return f"{summary}. Soll ich das machen?"


# ------------------------------------------------------------------ engine
class ConversationEngine:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        *,
        provider_name: str = "local",
        settings: Optional[Settings] = None,
        store: Optional[ConversationStore] = None,
        tool_names: Optional[List[str]] = None,
        usage_recorder: Any = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.provider_name = provider_name
        self.settings = settings or get_settings()
        self.store = store or conversations
        self.executor = ToolExecutor(registry, default_timeout=self.settings.TOOL_TIMEOUT_SECONDS)
        self.tool_names = tool_names or exposed_tool_names(self.settings, registry)
        self._usage_recorder = usage_recorder
        # Set while resuming an approved call: that turn's history is committed text-only.
        self._text_only_turn = False
        # A progress phrase ("Ich schaue nach.") is spoken at most once per turn.
        self._spoke_this_turn = False

    # -------------------------------------------------------------- public API
    async def run_turn(self, conversation_id: str, user_text: str, *, source: str = "voice", db: Any = None) -> AsyncIterator[EngineEvent]:
        self._text_only_turn = False
        self._spoke_this_turn = False
        conv = self.store.get(conversation_id)
        user_msg = {"role": "user", "content": f"{time_context()}\n{user_text.strip()}"}
        ctx = ToolContext(db=db, provider=self.provider_name, source=source, conversation_id=conversation_id, user_message=user_text)
        quick = self._quick_command(user_text, ctx)
        if quick is not None:
            async for event in self._run_quick(conv, user_msg, quick, ctx):
                yield event
            return
        messages = [{"role": "system", "content": system_prompt(source)}] + conv.window(self.settings.HISTORY_MAX_TOKENS) + [user_msg]
        new_messages: List[Dict[str, Any]] = [user_msg]
        guard = looks_like_action_request(user_text)
        # aclosing: if the caller stops early (barge-in), the inner loop's
        # finally block - which writes the interrupted turn to the history -
        # runs right now, not whenever the garbage collector gets to it.
        async with aclosing(self._loop(conv, messages, new_messages, ctx, start_step=1, seen_calls=set(), guard=guard)) as events:
            async for event in events:
                yield event

    async def resume(self, approval: pending_approvals.PendingApproval, approved: bool, *, db: Any = None) -> AsyncIterator[EngineEvent]:
        conv = self.store.get(approval.conversation_id or f"oneshot-{approval.approval_id}")
        ctx = ToolContext(db=db, provider=self.provider_name, source=approval.source, conversation_id=approval.conversation_id, user_message=approval.user_message, tainted=approval.tainted)
        plan = self.executor.plan(approval.tool_name, approval.arguments, ctx)
        if not approved:
            await self.executor.record_decision(plan, "rejected", ctx, approval_id=approval.approval_id)
            yield ToolFinished(approval.tool_name, approval.arguments, False, plan.summary, error="Vom Nutzer abgelehnt.")
            conv.messages.append({"role": "assistant", "content": REJECTED_REPLY})
            yield Sentence(REJECTED_REPLY, 0)
            yield TurnDone(reply=REJECTED_REPLY, steps_used=approval.steps_used)
            return

        messages = approval.messages
        new_messages: List[Dict[str, Any]] = []
        self._text_only_turn = True
        yield ToolStarted(approval.tool_name, plan.summary)
        result = await self.executor.execute(approval.tool_name, approval.arguments, ctx=ctx, approval_id=approval.approval_id)
        ctx.tainted = ctx.tainted or result.untrusted
        yield ToolFinished(approval.tool_name, approval.arguments, result.success, plan.summary, result.data, result.error)
        tool_msg = _tool_message(approval.tool_call_id, approval.tool_name, result)
        messages.append(tool_msg)
        new_messages.append(tool_msg)
        seen = {(approval.tool_name, json.dumps(approval.arguments, sort_keys=True))}
        if approval.remaining_calls:
            calls = [ToolCall(c["id"], c["name"], c["arguments"], c.get("raw")) for c in approval.remaining_calls]
            async with aclosing(self._run_calls(conv, messages, new_messages, ctx, calls, approval.steps_used, seen)) as events:
                async for event in events:
                    yield event
                    if isinstance(event, ApprovalRequired):
                        return
        async with aclosing(self._loop(conv, messages, new_messages, ctx, start_step=approval.steps_used + 1, seen_calls=seen)) as events:
            async for event in events:
                yield event

    # -------------------------------------------------------------- quick commands
    def _quick_command(self, user_text: str, ctx: ToolContext) -> Optional[QuickCommand]:
        quick = match_quick_command(user_text)
        if quick is None or quick.tool not in self.tool_names:
            return None
        plan = self.executor.plan(quick.tool, quick.arguments, ctx)
        if plan.error or plan.needs_approval:
            return None  # let the LLM path (with approval card) handle it
        return quick

    async def _run_quick(self, conv: Conversation, user_msg: Dict[str, Any], quick: QuickCommand, ctx: ToolContext) -> AsyncIterator[EngineEvent]:
        started = time.perf_counter()
        plan = self.executor.plan(quick.tool, quick.arguments, ctx)
        yield ToolStarted(quick.tool, plan.summary or quick.tool)
        result = await self.executor.execute(quick.tool, quick.arguments, ctx=ctx)
        yield ToolFinished(quick.tool, plan.arguments, result.success, plan.summary or quick.tool, result.data, result.error)
        reply = quick.reply if result.success else f"Hat nicht geklappt: {result.error}"
        elapsed = round(time.perf_counter() - started, 3)
        yield TextDelta(reply)
        yield Sentence(reply, 0)
        self._commit(conv, [user_msg], partial_reply=reply)
        yield TurnDone(reply=reply, steps_used=0, metrics={"quick_command": 1.0, "first_sentence": elapsed, "total": elapsed})

    # -------------------------------------------------------------- internals
    async def _loop(self, conv: Conversation, messages, new_messages, ctx: ToolContext, *, start_step: int, seen_calls: set, guard: bool = False) -> AsyncIterator[EngineEvent]:
        """`guard`: the user asked for an action - hold the first answer back
        until it is clear whether the model called a tool (action_guard.py)."""
        tools = to_openai_tools(self.registry, self.tool_names)
        started = time.perf_counter()
        turn_metrics: Dict[str, float] = {}
        sentence_index = 0
        spoken_parts: List[str] = []
        committed = False
        hinted = False

        def emit(kind: str, text: str) -> EngineEvent:
            nonlocal sentence_index
            if kind == "delta":
                return TextDelta(text)
            if "first_sentence" not in turn_metrics:
                turn_metrics["first_sentence"] = round(time.perf_counter() - started, 3)
            spoken_parts.append(text)
            self._spoke_this_turn = True
            event = Sentence(text, sentence_index)
            sentence_index += 1
            return event

        try:
            for step in range(start_step, self.settings.AGENT_MAX_STEPS + 1):
                splitter = SentenceSplitter()
                text_parts: List[str] = []
                calls: List[ToolCall] = []
                held: List[tuple] = []
                call_messages = messages
                if hinted:
                    # The hint only goes into this one request, never into the history.
                    last = messages[-1]
                    call_messages = messages[:-1] + [{**last, "content": f"{last['content']}\n{RETRY_HINT}"}]
                call_started = time.perf_counter()
                try:
                    async for event in stream_events(self.llm, call_messages, tools):
                        if isinstance(event, LLMTextDelta):
                            if "llm_first_token" not in turn_metrics:
                                turn_metrics["llm_first_token"] = round(time.perf_counter() - started, 3)
                            text_parts.append(event.text)
                            pieces = [("delta", event.text)] + [("sentence", s) for s in splitter.push(event.text)]
                            if guard:
                                held.extend(pieces)
                            else:
                                for kind, text in pieces:
                                    yield emit(kind, text)
                        elif isinstance(event, ToolCallEvent):
                            calls.append(event.call)
                        elif isinstance(event, StreamDone):
                            self._record_usage(ctx, event)
                            # Backend's own view of this call: model (re)load,
                            # prompt processing and how much of the prompt was
                            # NOT served from the prompt cache.
                            if "load" in event.timings and event.timings["load"] > 0.05:
                                turn_metrics[f"llm_{step}_load"] = round(event.timings["load"], 3)
                            if "prompt_eval" in event.timings:
                                turn_metrics[f"llm_{step}_prefill"] = round(event.timings["prompt_eval"], 3)
                            if event.usage is not None:
                                turn_metrics[f"llm_{step}_uncached_tokens"] = event.usage.prompt_tokens - event.usage.cached_prompt_tokens
                except LLMUnavailableError as exc:
                    err = errors.from_exception(exc)
                    logger.warning("LLM failed in turn: %s", exc)
                    self._commit(conv, new_messages, partial_reply=None, text_only=self._text_only_turn)
                    committed = True
                    yield TurnFailed(err)
                    return
                turn_metrics.setdefault("llm_calls", 0)
                turn_metrics["llm_calls"] += 1
                turn_metrics[f"llm_call_{step}"] = round(time.perf_counter() - call_started, 3)
                tail = [("sentence", s) for s in splitter.flush()]
                if guard:
                    held.extend(tail)
                    if not calls and claims_action("".join(text_parts)):
                        # "Notiz gespeichert." without save_note: held back, never spoken.
                        logger.warning("Model claimed an action without calling a tool%s: %r", " (again)" if hinted else "", "".join(text_parts)[:160])
                        turn_metrics["guard_retries"] = turn_metrics.get("guard_retries", 0) + 1
                        if not hinted:
                            hinted = True
                            continue
                        self._commit(conv, new_messages, partial_reply=NOT_DONE_REPLY, text_only=self._text_only_turn)
                        committed = True
                        yield emit("delta", NOT_DONE_REPLY)
                        yield emit("sentence", NOT_DONE_REPLY)
                        turn_metrics["total"] = round(time.perf_counter() - started, 3)
                        yield TurnDone(reply=NOT_DONE_REPLY, steps_used=step, metrics=turn_metrics)
                        return
                    guard = False
                    for kind, text in held:
                        yield emit(kind, text)
                else:
                    for kind, text in tail:
                        yield emit(kind, text)

                assistant_msg: Dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
                if calls:
                    assistant_msg["tool_calls"] = [c.to_openai() for c in calls]
                messages.append(assistant_msg)
                new_messages.append(assistant_msg)

                if not calls:
                    reply = "".join(text_parts).strip()
                    self._commit(conv, new_messages, text_only=self._text_only_turn)
                    committed = True
                    turn_metrics["total"] = round(time.perf_counter() - started, 3)
                    yield TurnDone(reply=reply, steps_used=step, metrics=turn_metrics)
                    return

                async with aclosing(self._run_calls(conv, messages, new_messages, ctx, calls, step, seen_calls)) as call_events:
                    async for event in call_events:
                        if isinstance(event, ApprovalRequired):
                            committed = True
                            yield event
                            return
                        yield event

            self._commit(conv, new_messages, partial_reply=STEP_LIMIT_REPLY, text_only=self._text_only_turn)
            committed = True
            yield emit("sentence", STEP_LIMIT_REPLY)
            yield TurnDone(reply=STEP_LIMIT_REPLY, steps_used=self.settings.AGENT_MAX_STEPS, metrics=turn_metrics)
        finally:
            if not committed:
                # Cancelled (barge-in) or crashed mid-turn: keep what was said so
                # the next turn knows the answer was cut off.
                partial = " ".join(spoken_parts)
                self._commit(conv, new_messages, partial_reply=(partial + INTERRUPTED_MARK) if partial else None, interrupted=True, text_only=self._text_only_turn)

    async def _run_calls(self, conv, messages, new_messages, ctx: ToolContext, calls: List[ToolCall], step: int, seen_calls: set) -> AsyncIterator[EngineEvent]:
        for i, call in enumerate(calls):
            if not call.arguments_valid:
                result = ToolResult(success=False, error=f"Arguments for '{call.name}' were not valid JSON - call the tool again with a JSON object.")
            else:
                signature = (call.name, json.dumps(call.arguments, sort_keys=True))
                plan = self.executor.plan(call.name, call.arguments, ctx)
                if signature in seen_calls:
                    result = ToolResult(success=False, error=f"'{call.name}' was already called with these arguments in this turn - use its result instead of calling it again.")
                elif plan.needs_approval:
                    remaining = [{"id": c.id, "name": c.name, "arguments": c.arguments, "raw": c.raw_arguments} for c in calls[i + 1 :]]
                    approval_id = pending_approvals.create(
                        tool_name=call.name,
                        arguments=plan.arguments,
                        tool_call_id=call.id,
                        messages=messages,
                        call_log=[],
                        steps_used=step,
                        remaining_calls=remaining,
                        risk=plan.risk.value if plan.risk else "write",
                        summary=plan.summary,
                        provider=self.provider_name,
                        source=ctx.source,
                        conversation_id=ctx.conversation_id,
                        user_message=ctx.user_message,
                        tainted=ctx.tainted,
                    )
                    await self.executor.record_decision(plan, "approval_requested", ctx, approval_id=approval_id)
                    prompt = spoken_approval_prompt(plan.summary)
                    # Only the spoken question goes into the history; the full
                    # working state (incl. the tool call) lives in the approval
                    # and resume() continues from there.
                    self._commit(conv, new_messages, partial_reply=prompt, text_only=True)
                    yield ApprovalRequired(approval_id, call.name, plan.arguments, plan.summary, plan.risk.value if plan.risk else "write", pending_approvals.ttl_seconds(), prompt, step)
                    return
                else:
                    seen_calls.add(signature)
                    yield ToolStarted(call.name, plan.summary or call.name)
                    progress = getattr(plan.tool, "spoken_progress", None)
                    if progress and ctx.source == "voice" and not self._spoke_this_turn:
                        self._spoke_this_turn = True
                        yield Sentence(progress, -1)
                    result = await self.executor.execute(call.name, call.arguments, ctx=ctx)
                    yield ToolFinished(call.name, call.arguments, result.success, plan.summary or call.name, result.data, result.error)
            ctx.tainted = ctx.tainted or result.untrusted
            tool_msg = _tool_message(call.id, call.name, result)
            messages.append(tool_msg)
            new_messages.append(tool_msg)

    def _commit(
        self,
        conv: Conversation,
        new_messages: List[Dict[str, Any]],
        partial_reply: Optional[str] = None,
        interrupted: bool = False,
        text_only: bool = False,
    ) -> None:
        msgs = list(new_messages)
        if text_only:
            # Approval turns: keep the conversation readable (user/assistant text)
            # without half tool exchanges that would be invalid on their own.
            msgs = [m for m in msgs if m["role"] == "user" or (m["role"] == "assistant" and not m.get("tool_calls") and m.get("content"))]
        # An assistant message with tool calls must be followed by its tool
        # results; a cut-off turn may end right after the call - drop it.
        while msgs and msgs[-1]["role"] == "assistant" and msgs[-1].get("tool_calls"):
            msgs.pop()
        if partial_reply:
            if msgs and msgs[-1]["role"] == "assistant" and not msgs[-1].get("tool_calls"):
                msgs[-1] = {"role": "assistant", "content": partial_reply}
            else:
                msgs.append({"role": "assistant", "content": partial_reply})
        if interrupted and msgs and msgs[-1]["role"] == "user":
            msgs.append({"role": "assistant", "content": _INTERRUPTED_BEFORE_ANSWER})
        conv.messages.extend(msgs)
        conv.compact_tool_results()
        conv.updated_at = time.time()
        new_messages.clear()

    def _record_usage(self, ctx: ToolContext, done: StreamDone) -> None:
        if done.usage is None or ctx.db is None:
            return
        try:
            from app.settings_store import record_token_usage  # noqa: PLC0415

            record_token_usage(ctx.db, self.provider_name, self.llm.model, done.usage.as_openai_dict())
        except Exception:  # noqa: BLE001 - usage stats must never break a turn
            logger.exception("Could not record token usage")


def new_conversation_id() -> str:
    return uuid.uuid4().hex[:16]


def new_turn_pipeline_id() -> str:
    pid = uuid.uuid4().hex[:10]
    pipeline_id_var.set(pid)
    return pid


def record_turn_metrics(m: Dict[str, float]) -> None:
    for key in ("llm_first_token", "first_sentence", "total"):
        if key in m:
            metrics.record(f"turn_{key}", m[key])
