"""Test doubles for LLM providers.

Many tests script the model as a list of finished assistant messages
(OpenAI chat format). OneShotLLM turns such a scripted one-shot answer into
the streaming interface every real provider implements (stream_chat).
"""
from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional

from app.llm.base import LLMProvider, StreamDone, TextDelta, ToolCall, ToolCallEvent, Usage, new_tool_call_id, parse_tool_arguments


class ChatWithToolsResponse(NamedTuple):
    message: Dict[str, Any]
    usage: Optional[Dict[str, int]]


class OneShotLLM(LLMProvider):
    """Subclasses implement `chat_with_tools(messages, tools)` -> ChatWithToolsResponse."""

    async def chat_with_tools(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], temperature: float = 0.2) -> ChatWithToolsResponse:
        raise NotImplementedError

    async def stream_chat(self, messages, tools=None, *, temperature=None, max_tokens=None):
        response = await self.chat_with_tools(messages, tools or [])
        content = response.message.get("content") or ""
        if content:
            yield TextDelta(content)
        for tc in response.message.get("tool_calls") or []:
            fn = tc.get("function", {})
            args, raw = parse_tool_arguments(fn.get("arguments"))
            yield ToolCallEvent(ToolCall(id=tc.get("id") or new_tool_call_id(), name=fn.get("name", ""), arguments=args, raw_arguments=raw))
        usage = response.usage or {}
        yield StreamDone(usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)) if usage else None)
