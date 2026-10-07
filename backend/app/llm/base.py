"""
LLM provider abstraction.

Two layers live here:

1. The streaming interface (`stream_chat`) every provider implements and
   the conversation engine uses: an async iterator of `LLMEvent`s
   (TextDelta / ToolCallEvent / StreamDone). Streaming is the default
   because voice output starts speaking at the first sentence boundary.

2. The legacy convenience methods `chat()` and `chat_with_tools()` that
   the older pipelines were written against. They are thin collectors
   over `stream_chat` in new providers, so there is only one real code
   path per backend.

Messages use one canonical (OpenAI-style) shape inside Cypher:

    {"role": "system"|"user"|"assistant"|"tool", "content": str,
     "tool_calls": [{"id", "type": "function", "function": {"name", "arguments": <JSON string>}}],
     "tool_call_id": str, "name": str}

Each provider converts to its backend's wire format.
"""
from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, NamedTuple, Optional, TypedDict, Union


class ChatMessage(TypedDict):
    role: str  # "system" | "user" | "assistant"
    content: str


# ---------------------------------------------------------------- errors
class LLMUnavailableError(Exception):
    """The LLM backend cannot be reached (connection refused, DNS, ...).

    Subclasses narrow down the cause so app/core/errors.py can give the
    user the right German hint. Kept as the common base class because
    a lot of existing code catches exactly this type.
    """

    def __init__(self, message: str = "LLM is unavailable.", *, provider: str = "local", base_url: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.base_url = base_url


class ModelNotFoundError(LLMUnavailableError):
    def __init__(self, model: str, *, provider: str = "local", base_url: str = "") -> None:
        super().__init__(f"Model '{model}' is not installed.", provider=provider, base_url=base_url)
        self.model = model


class LLMTimeoutError(LLMUnavailableError):
    def __init__(self, timeout_seconds: float, *, provider: str = "local", base_url: str = "") -> None:
        super().__init__(f"LLM did not respond within {timeout_seconds:.0f}s.", provider=provider, base_url=base_url)
        self.timeout_seconds = timeout_seconds


class LLMResponseError(LLMUnavailableError):
    """The backend answered, but with an error or an unparseable body."""


# ---------------------------------------------------------------- stream events
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]
    # Raw argument text if it could not be parsed as a JSON object - lets
    # the engine ask the model to retry instead of silently using {}.
    raw_arguments: Optional[str] = None

    @property
    def arguments_valid(self) -> bool:
        return self.raw_arguments is None

    def to_openai(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.raw_arguments if self.raw_arguments is not None else json.dumps(self.arguments, ensure_ascii=False)},
        }


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_openai_dict(self) -> Dict[str, int]:
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens, "total_tokens": self.total_tokens}


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallEvent:
    call: ToolCall


@dataclass
class StreamDone:
    usage: Optional[Usage] = None
    done_reason: str = "stop"
    # Backend-reported timings in seconds (Ollama: load/prompt/eval).
    timings: Dict[str, float] = field(default_factory=dict)


LLMEvent = Union[TextDelta, ToolCallEvent, StreamDone]


@dataclass
class LLMResponse:
    """A fully collected stream."""

    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Optional[Usage] = None
    done_reason: str = "stop"
    timings: Dict[str, float] = field(default_factory=dict)

    def to_openai_message(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_openai() for tc in self.tool_calls]
        return msg


async def collect(stream: AsyncIterator[LLMEvent]) -> LLMResponse:
    out = LLMResponse()
    parts: List[str] = []
    async for event in stream:
        if isinstance(event, TextDelta):
            parts.append(event.text)
        elif isinstance(event, ToolCallEvent):
            out.tool_calls.append(event.call)
        elif isinstance(event, StreamDone):
            out.usage = event.usage
            out.done_reason = event.done_reason
            out.timings = event.timings
    out.content = "".join(parts)
    return out


async def stream_events(llm: Any, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> AsyncIterator["LLMEvent"]:
    """Stream from any provider-like object: uses `stream_chat` if present,
    otherwise adapts a one-shot `chat_with_tools` (simple test doubles and
    third-party providers only implement that)."""
    stream = getattr(llm, "stream_chat", None)
    if stream is not None:
        async for event in stream(messages, tools):
            yield event
        return
    response = await llm.chat_with_tools(messages, tools or [])
    content = response.message.get("content") or ""
    if content:
        yield TextDelta(content)
    for tc in response.message.get("tool_calls") or []:
        fn = tc.get("function", {})
        args, raw = parse_tool_arguments(fn.get("arguments"))
        yield ToolCallEvent(ToolCall(id=tc.get("id") or new_tool_call_id(), name=fn.get("name", ""), arguments=args, raw_arguments=raw))
    usage = response.usage or {}
    yield StreamDone(usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)) if usage else None)


def new_tool_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


def parse_tool_arguments(raw: Any) -> tuple[Dict[str, Any], Optional[str]]:
    """Normalize tool-call arguments to a dict.

    Returns (arguments, raw_if_invalid). Ollama sends a JSON object,
    OpenAI a JSON string; a confused model can send garbage either way.
    """
    if isinstance(raw, dict):
        return raw, None
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}, raw
        if isinstance(parsed, dict):
            return parsed, None
        return {}, raw
    return {}, json.dumps(raw, default=str)


# ---------------------------------------------------------------- legacy return type
class ChatWithToolsResponse(NamedTuple):
    """Return shape of the legacy `chat_with_tools()`.

    `message` is an OpenAI-format assistant message (tool-call arguments
    as JSON strings), `usage` the {"prompt_tokens", "completion_tokens",
    "total_tokens"} dict or None if the backend didn't report it.
    """

    message: Dict[str, Any]
    usage: Optional[Dict[str, int]]


class LLMProvider(ABC):
    """Base class for chat backends.

    Every provider exposes `model: str` and `name: str` ("local"/"cloud").
    `chat()` and `health_check()` are abstract for historical reasons
    (test doubles of the old pipeline implement only those).
    """

    name: str = "local"
    model: str = ""

    @abstractmethod
    async def chat(self, messages: List[ChatMessage], temperature: float = 0.3) -> str:
        raise NotImplementedError

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.2,
    ) -> ChatWithToolsResponse:
        raise NotImplementedError(f"{type(self).__name__} does not support tool-calling.")

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[LLMEvent]:
        """Default for providers that only implement chat_with_tools():
        one non-streamed call, re-emitted as events."""
        response = await self.chat_with_tools(messages, tools or [], temperature=temperature if temperature is not None else 0.2)
        content = response.message.get("content") or ""
        if content:
            yield TextDelta(content)
        for tc in response.message.get("tool_calls") or []:
            fn = tc.get("function", {})
            args, raw = parse_tool_arguments(fn.get("arguments"))
            yield ToolCallEvent(ToolCall(id=tc.get("id") or new_tool_call_id(), name=fn.get("name", ""), arguments=args, raw_arguments=raw))
        usage = response.usage or {}
        yield StreamDone(usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)) if usage else None)

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError
