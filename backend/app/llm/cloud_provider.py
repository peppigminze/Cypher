"""
Cloud LLM provider - OpenAI Responses API via the official SDK (M5).

Checked against the official docs (developers.openai.com, 2026-10-08):
  * Responses API, not Chat Completions: OpenAI recommends it, and Chat
    Completions does NOT support function calling with GPT-6 Astra or
    GPT-6.1 Sol - with Responses every current model can use Cypher's tools.
  * Tools: {"type": "function", "name", "description", "parameters"}.
    strict=False because Cypher's schemas have optional parameters
    (strict mode requires every property to be required).
  * Tool round trip: the model's {"type": "function_call", call_id, name,
    arguments} goes back together with {"type": "function_call_output",
    call_id, output}. For reasoning models the reasoning items of that
    response must be sent back too.
  * store=False: OpenAI keeps nothing of Cypher's conversations. Reasoning
    items are then requested as include=["reasoning.encrypted_content"]
    and kept here (in memory, per call_id) until the tool result goes back.
  * Streaming events used: response.output_text.delta, response.output_item.done
    (function_call / reasoning items), response.completed (usage:
    input_tokens, input_tokens_details.cached_tokens, output_tokens),
    response.failed / response.incomplete / error.
  * reasoning={"effort": ...}: GPT-6 Luna supports "none"; Cypher's default
    "low" keeps voice answers quick (CLOUD_LLM_REASONING_EFFORT).
  * No temperature: the reasoning models don't take it.

The engine keeps its history in the OpenAI chat-message format (role/content,
tool_calls, role=tool); translate_messages() maps that to Responses input
items, so nothing else in Cypher knows which API is behind "cloud".

Privacy: everything in the prompt (conversation, memory, tool results)
leaves the PC when the cloud is used - that's why it is never used without
an explicit choice (Settings toggle, or "ja" to the fallback question).
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import openai

from app.llm.base import (
    LLMEvent,
    LLMProvider,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    Usage,
    collect,
    parse_tool_arguments,
)

logger = logging.getLogger("cypher.llm.cloud")

# Reasoning items to send back with a tool result, by call_id (bounded).
_REASONING_FOR_CALL: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
_MAX_REMEMBERED_CALLS = 256


class CloudNotConfigured(LLMUnavailableError):
    """Cloud selected, but no API key in .env."""


class CloudAuthError(LLMUnavailableError):
    """The API key was rejected."""


class CloudRateLimitError(LLMUnavailableError):
    """Rate limit or exhausted quota at the cloud provider."""


class CloudBudgetExceeded(LLMUnavailableError):
    """CLOUD_MONTHLY_BUDGET_USD is used up (app/llm/pricing.py)."""

    def __init__(self, spent_usd: float, budget_usd: float) -> None:
        super().__init__("Monthly cloud budget exceeded.", provider="cloud")
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd


def _remember_reasoning(call_id: str, items: List[Dict[str, Any]]) -> None:
    if not items:
        return
    _REASONING_FOR_CALL[call_id] = items
    while len(_REASONING_FOR_CALL) > _MAX_REMEMBERED_CALLS:
        _REASONING_FOR_CALL.popitem(last=False)


def translate_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out = []
    for tool in tools or []:
        fn = tool.get("function", tool)
        out.append({
            "type": "function",
            "name": fn["name"],
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            "strict": False,
        })
    return out


def translate_messages(messages: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """Chat-format history -> (instructions, Responses input items)."""
    instructions: List[str] = []
    items: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "system":
            instructions.append(content)
        elif role == "user":
            items.append({"role": "user", "content": content})
        elif role == "assistant":
            if content:
                items.append({"role": "assistant", "content": content})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function", {})
                arguments = fn.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments or {}, ensure_ascii=False)
                items.extend(_REASONING_FOR_CALL.get(call.get("id", ""), []))
                items.append({"type": "function_call", "call_id": call.get("id", ""), "name": fn.get("name", ""), "arguments": arguments})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": msg.get("tool_call_id", ""), "output": content})
    return "\n\n".join(instructions), items


class CloudLLMProvider(LLMProvider):
    name = "cloud"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        reasoning_effort: Optional[str] = "low",
        client: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        # The key lives only inside the SDK client; it is never logged.
        self._client = client or openai.AsyncOpenAI(api_key=api_key, base_url=self.base_url, timeout=timeout_seconds, max_retries=1)

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[LLMEvent]:
        instructions, items = translate_messages(messages)
        params: Dict[str, Any] = {
            "model": self.model,
            "input": items,
            "stream": True,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if instructions:
            params["instructions"] = instructions
        if tools:
            params["tools"] = translate_tools(tools)
        if self.reasoning_effort:
            params["reasoning"] = {"effort": self.reasoning_effort}
        if max_tokens:
            params["max_output_tokens"] = max_tokens

        reasoning_items: List[Dict[str, Any]] = []
        try:
            stream = await self._client.responses.create(**params)
            async for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    if event.delta:
                        yield TextDelta(event.delta)
                elif kind == "response.output_item.done":
                    item = event.item
                    if item.type == "reasoning":
                        reasoning_items.append(item.model_dump(exclude_none=True))
                    elif item.type == "function_call":
                        args, raw = parse_tool_arguments(item.arguments)
                        # Reasoning that led to this call must accompany its result.
                        _remember_reasoning(item.call_id, reasoning_items)
                        reasoning_items = []
                        yield ToolCallEvent(ToolCall(id=item.call_id, name=item.name, arguments=args, raw_arguments=raw))
                elif kind == "response.completed":
                    yield StreamDone(usage=_usage(event.response), done_reason="stop")
                elif kind == "response.incomplete":
                    reason = getattr(getattr(event.response, "incomplete_details", None), "reason", None) or "incomplete"
                    yield StreamDone(usage=_usage(event.response), done_reason=str(reason))
                elif kind == "response.failed":
                    error = getattr(event.response, "error", None)
                    raise LLMResponseError(f"Cloud response failed: {getattr(error, 'message', 'unknown error')}", provider="cloud")
                elif kind == "error":
                    raise LLMResponseError(f"Cloud stream error: {getattr(event, 'message', '')}", provider="cloud")
        except openai.AuthenticationError as exc:
            raise CloudAuthError("Cloud API key was rejected.", provider="cloud", base_url=self.base_url) from exc
        except openai.RateLimitError as exc:
            raise CloudRateLimitError("Cloud rate limit or quota reached.", provider="cloud", base_url=self.base_url) from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError(self.timeout_seconds, provider="cloud", base_url=self.base_url) from exc
        except openai.APIConnectionError as exc:
            raise LLMUnavailableError("Cloud LLM is unreachable.", provider="cloud", base_url=self.base_url) from exc
        except openai.APIStatusError as exc:
            logger.warning("Cloud LLM HTTP %s: %s", exc.status_code, getattr(exc, "message", ""))
            raise LLMResponseError(f"Cloud LLM HTTP {exc.status_code}", provider="cloud", base_url=self.base_url) from exc

    async def chat(self, messages: List[Dict[str, Any]], temperature: float = 0.3) -> str:
        return (await collect(self.stream_chat(messages))).content

    async def health_check(self) -> bool:
        try:
            await self._client.models.retrieve(self.model)
            return True
        except Exception:  # noqa: BLE001
            return False


def _usage(response: Any) -> Optional[Usage]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    details = getattr(usage, "input_tokens_details", None)
    return Usage(
        prompt_tokens=int(usage.input_tokens or 0),
        completion_tokens=int(usage.output_tokens or 0),
        cached_prompt_tokens=int(getattr(details, "cached_tokens", 0) or 0),
    )
