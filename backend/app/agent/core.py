"""
Cypher Agent Core.

Pipeline (see project spec section 7):
    understand -> plan -> select_tools -> execute_tools -> observe
        -> (loop: additional tool calls if needed, bounded by
            MAX_AGENT_STEPS so a confused model can never loop forever -
            see project spec section 35)
    -> generate_response -> save_memory

V1 does not have real LLM function-calling for every local model, so
tool selection uses a small JSON protocol: the model is asked to
reply with a JSON object describing which tool (if any) to call, a
"done" flag, and a natural-language reply. This keeps Cypher
provider-agnostic and works with any local chat model, not just ones
with native tool use.

Tool security (project spec section 14): if the model picks a
CONFIRM_REQUIRED tool, the pipeline does NOT execute it. It returns a
PipelineResult with done=False describing the pending tool call; the
caller (agent/sync_worker.py) persists that as a PendingAction for a
human to confirm/reject via the PWA, and later resumes the pipeline
with the prior observations once approved.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database.models import Message, MessageStatus
from app.llm.base import ChatMessage, LLMProvider, LLMUnavailableError
from app.memory.store import MemoryStore
from app.persona import CYPHER_PERSONA
from app.tools.base import Tool, ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger("cypher.agent")

# Hard cap on tool-call steps per user request. Prevents a confused
# local model from looping forever (project spec section 35).
MAX_AGENT_STEPS = 5


SYSTEM_PROMPT_TEMPLATE = CYPHER_PERSONA + """
The current date and time is: {now_iso} (UTC). When a tool needs an
absolute date/time (e.g. due_at) and the user gave a relative one
("tomorrow", "in 30 minutes", "next Friday at 5pm"), compute the
correct absolute ISO-8601 UTC datetime yourself using this reference -
never pass the relative phrase through unresolved.

You solve the user's request step by step. On each step you may either
call exactly one tool to gather information or perform an action, or
finish and answer the user directly. You may call multiple tools across
several steps if one isn't enough (e.g. look something up, then act on
what you found).

Available tools:
{tool_list}

Relevant memories about the user (may be empty):
{memories}

Observations from tools you already called for this request, most
recent last (may be empty if this is your first step):
{observations}

Respond with ONLY a single JSON object, no other text, in this exact shape:
{{
  "tool": "<tool_name or null>",
  "arguments": {{}},
  "done": <true if "reply" is your final answer to the user, false if you
           are calling a tool and want to see its result before continuing>,
  "reply": "<natural-language text, per the tone/length rules above: the
             final answer if done=true, otherwise a short status update
             the user may briefly see>"
}}

Call at most one tool per step. If no tool is needed, set "tool" to null
and "done" to true. NEVER call the same tool with the same arguments
twice - if the observations already show it succeeded, set "done" to
true immediately instead of calling it again. As soon as the user's
request is fulfilled, set "done" to true - do not keep calling tools
"just in case".
"""

FINAL_ANSWER_PROMPT_TEMPLATE = CYPHER_PERSONA + """
The user asked: "{user_message}"

Here is what you did and found, as a JSON list of steps (each with the
tool name, arguments, and either a result or an error):
{observations}

Write the final reply to the user summarizing what happened, following
the tone/length rules above - a short confirmation is enough for a
routine successful action, don't pad it into a paragraph. Do not mention
JSON, internal tool names, or step numbers explicitly. If something
failed, say so honestly instead of pretending it worked. Reply with
plain text only.
"""


@dataclass
class PlanDecision:
    tool: Optional[str]
    arguments: dict
    reply: str
    done: bool = True


@dataclass
class PipelineResult:
    """Outcome of running (or resuming) the agent pipeline for one message.

    done=True   -> `reply` is the final answer; the message is complete.
    done=False  -> the pipeline paused on a CONFIRM_REQUIRED tool;
                   `pending_tool`/`pending_arguments` describe it and
                   `reply` is a short status update to show the user
                   while they decide.
    `observations` always reflects every tool call made so far in this
    request, so the pipeline can be resumed exactly where it paused.
    """

    done: bool
    reply: str
    observations: List[dict] = field(default_factory=list)
    pending_tool: Optional[str] = None
    pending_arguments: Optional[dict] = None
    # "local" or "cloud" - only meaningful once at least one LLM call
    # happened in this run (i.e. usually once done=True, or if paused
    # after a planning call already went through _chat()).
    processed_by: Optional[str] = None


class CypherAgent:
    def __init__(self, llm: LLMProvider, tools: ToolRegistry, cloud_llm: Optional[LLMProvider] = None):
        self.llm = llm
        self.tools = tools
        # Optional cloud fallback (project spec sections 8/9) - only
        # ever tried when `llm` itself raises LLMUnavailableError, never
        # as a first choice. None unless the user explicitly enabled it
        # and configured an API key (see app/llm/factory.py).
        self.cloud_llm = cloud_llm
        # Set by _chat() on every call, reset at the start of each
        # run_pipeline() - tracks whether *this* request ever fell back
        # to cloud, so the final PipelineResult can report it (project
        # spec section 21: PWA shows 🖥️ Local / ☁️ Cloud per message).
        self._used_cloud_this_run = False

    async def _chat(self, messages: List[ChatMessage], temperature: float = 0.3) -> str:
        """LOCAL FIRST, always: try the primary (local) LLM, and only if
        it's unreachable AND a cloud fallback is configured, try that
        instead. If neither works, the original local error propagates."""
        try:
            return await self.llm.chat(messages, temperature=temperature)
        except LLMUnavailableError:
            if self.cloud_llm is None:
                raise
            logger.warning("Local LLM unavailable - falling back to cloud LLM.")
            self._used_cloud_this_run = True
            return await self.cloud_llm.chat(messages, temperature=temperature)

    # ---------------------------------------------------------- receive

    def receive_message(self, db: Session, content: str, client_id: str | None = None) -> Message:
        """Persist an incoming message as 'pending'. Idempotent on client_id."""
        if client_id:
            existing = db.query(Message).filter(Message.client_id == client_id).first()
            if existing:
                return existing

        message = Message(content=content, client_id=client_id, status=MessageStatus.pending)
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    # ---------------------------------------------------------- understand

    def understand(self, db: Session, content: str) -> dict:
        """Gather context: relevant memories for this message."""
        store = MemoryStore(db)
        memories = store.search(content, limit=5)
        return {"memories": [m.content for m in memories]}

    # ---------------------------------------------------------- plan

    async def plan(self, content: str, context: dict, observations: Optional[List[dict]] = None) -> PlanDecision:
        tool_list = "\n".join(
            f"- {t.name}: {t.description} (parameters: {json.dumps(t.parameters)})"
            for t in self.tools.list_tools()
        )
        memories = "\n".join(f"- {m}" for m in context.get("memories", [])) or "(none)"
        observations_text = json.dumps(observations, default=str) if observations else "(none yet)"

        now_iso = datetime.now(timezone.utc).isoformat()
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            tool_list=tool_list, memories=memories, observations=observations_text, now_iso=now_iso
        )
        messages: list[ChatMessage] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        raw = await self._chat(messages, temperature=0.2)
        return self._parse_plan(raw)

    def _parse_plan(self, raw: str) -> PlanDecision:
        text = raw.strip()
        # Be defensive: some local models wrap JSON in ```json fences.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            data = json.loads(text)
            tool = data.get("tool") or None
            arguments = data.get("arguments") or {}
            reply = data.get("reply") or ""
            done = bool(data.get("done", True))
            return PlanDecision(tool=tool, arguments=arguments, reply=reply, done=done)
        except (json.JSONDecodeError, AttributeError):
            # Model didn't follow the JSON protocol - fall back to treating
            # the whole output as a plain reply with no tool call.
            logger.warning("Could not parse plan JSON, falling back to plain reply.")
            return PlanDecision(tool=None, arguments={}, reply=raw.strip(), done=True)

    # ---------------------------------------------------------- select_tools

    def select_tools(self, decision: PlanDecision) -> Optional[Tool]:
        """Returns the tool to execute, or None if there is nothing to
        run automatically right now (no tool requested, unknown tool
        name, or the tool requires human confirmation)."""
        if not decision.tool:
            return None
        tool = self.tools.get(decision.tool)
        if tool is None:
            logger.warning("Model requested unknown tool '%s'", decision.tool)
            return None
        if not self.tools.is_executable_automatically(decision.tool):
            logger.info("Tool '%s' requires confirmation - pausing pipeline.", decision.tool)
            return None
        return tool

    # ---------------------------------------------------------- execute_tools

    async def execute_tools(self, db: Session, tool: Tool, arguments: dict, approval_id: Optional[str] = None) -> ToolResult:
        """Runs through the shared ToolExecutor (validation, risk tiers,
        timeout, audit log) - the old pipeline gets no private shortcut.
        `approval_id` is set only for actions a human confirmed in the PWA."""
        from app.tools.context import ToolContext  # noqa: PLC0415
        from app.tools.executor import ToolExecutor  # noqa: PLC0415

        ctx = ToolContext(db=db, source="legacy", provider="local")
        return await ToolExecutor(self.tools).execute(tool.name, arguments, db=db, ctx=ctx, approval_id=approval_id)

    @staticmethod
    def _already_attempted(observations: List[dict], tool_name: str, arguments: dict) -> bool:
        """True if this exact (tool, arguments) pair already has an
        observation, success OR failure. Retrying an identical call that
        already succeeded would duplicate a side effect; retrying one
        that already failed can't magically succeed the second time and
        - for a CONFIRM_REQUIRED tool - would otherwise re-pause the
        pipeline on the same doomed action forever, asking the human to
        confirm the same failing write_file/run_command repeatedly.
        Verified live: without this, a write_file call rejected by the
        ALLOWED_DIRECTORIES sandbox got retried unchanged by
        llama3.1:8b and created a second, identical confirmation request."""
        return any(o.get("tool") == tool_name and o.get("arguments") == arguments for o in observations)

    @staticmethod
    def build_observation(tool_name: str, arguments: dict, result: ToolResult) -> dict:
        if result.success:
            return {"tool": tool_name, "arguments": arguments, "result": result.data}
        return {"tool": tool_name, "arguments": arguments, "error": result.error}

    # ---------------------------------------------------------- generate_response

    async def _summarize(self, content: str, observations: List[dict], truncated: bool = False) -> str:
        prompt = FINAL_ANSWER_PROMPT_TEMPLATE.format(
            user_message=content, observations=json.dumps(observations, default=str)
        )
        if truncated:
            prompt += (
                "\n\nHinweis: Das Schritt-Limit wurde erreicht, bevor alles abgeschlossen war. "
                "Sag dem Nutzer ehrlich, was du bereits herausgefunden/getan hast und was noch offen ist."
            )
        try:
            summary = await self._chat(
                [
                    {"role": "system", "content": "You write short, friendly assistant replies."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
            )
            return summary.strip()
        except LLMUnavailableError:
            # Tool(s) already ran - degrade gracefully to a plain summary
            # instead of failing the whole message.
            return f"Erledigt. Ergebnisse: {json.dumps(observations, default=str)}"

    def save_memory(self, db: Session, content: str, category: str | None = None) -> None:
        """Explicit helper for callers that want to persist a memory
        directly (the LLM can also do this itself via the save_memory
        tool during planning)."""
        MemoryStore(db).save(content=content, category=category)

    # ---------------------------------------------------------- pipeline

    async def run_pipeline(
        self,
        db: Session,
        content: str,
        observations: Optional[List[dict]] = None,
        processed_by: Optional[str] = None,
    ) -> PipelineResult:
        """Run (or resume) the understand -> plan -> act loop for a
        message and return its outcome. Bounded by MAX_AGENT_STEPS,
        counting steps already taken if resuming after a confirmation.

        `processed_by` carries forward whether an earlier segment of
        this same message (before a confirmation pause) already fell
        back to cloud, so a resumed run doesn't forget and under-report
        "local" for a message that did use cloud earlier on.

        Raises LLMUnavailableError if the local LLM cannot be reached.
        """
        self._used_cloud_this_run = processed_by == "cloud"
        context = self.understand(db, content)
        observations = list(observations or [])
        steps_taken = len(observations)

        while steps_taken < MAX_AGENT_STEPS:
            decision = await self.plan(content, context, observations)

            if not decision.tool:
                reply = await self._summarize(content, observations) if observations else (decision.reply or "...")
                return PipelineResult(done=True, reply=reply, observations=observations, processed_by=self._provider())

            tool = self.tools.get(decision.tool)
            if tool is None:
                observations.append(
                    {
                        "tool": decision.tool,
                        "arguments": decision.arguments,
                        "error": f"Unknown tool '{decision.tool}'.",
                    }
                )
                steps_taken += 1
                continue

            if self._already_attempted(observations, decision.tool, decision.arguments):
                logger.warning(
                    "Model repeated an identical call to '%s' that was already attempted - "
                    "stopping instead of re-running or re-pausing on it.",
                    decision.tool,
                )
                reply = await self._summarize(content, observations)
                return PipelineResult(done=True, reply=reply, observations=observations, processed_by=self._provider())

            if not self.tools.is_executable_automatically(decision.tool):
                return PipelineResult(
                    done=False,
                    reply=decision.reply or f"Ich möchte '{decision.tool}' ausführen. Bitte bestätige das kurz.",
                    observations=observations,
                    pending_tool=decision.tool,
                    pending_arguments=decision.arguments,
                    processed_by=self._provider(),
                )

            tool_result = await self.execute_tools(db, tool, decision.arguments)
            observations.append(self.build_observation(decision.tool, decision.arguments, tool_result))
            steps_taken += 1

            if decision.done:
                reply = await self._summarize(content, observations)
                return PipelineResult(done=True, reply=reply, observations=observations, processed_by=self._provider())

        logger.warning("Agent exceeded MAX_AGENT_STEPS=%d for a single request.", MAX_AGENT_STEPS)
        reply = await self._summarize(content, observations, truncated=True)
        return PipelineResult(done=True, reply=reply, observations=observations, processed_by=self._provider())

    def _provider(self) -> str:
        return "cloud" if self._used_cloud_this_run else "local"
