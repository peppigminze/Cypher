from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, NamedTuple, Optional, TypedDict


class ChatMessage(TypedDict):
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMUnavailableError(Exception):
    """Raised when the configured LLM backend cannot be reached."""


class ChatWithToolsResponse(NamedTuple):
    """Return shape for LLMProvider.chat_with_tools().

    `message` is the raw OpenAI-format assistant message (may contain
    `tool_calls`) - see app/agent/native_tool_agent.py for how the
    calling loop uses it. `usage` is the backend's own token count for
    this one call ({"prompt_tokens", "completion_tokens",
    "total_tokens"}), or None if the backend didn't report it - not
    every OpenAI-compatible server does. Phase 7's
    app/settings_store.py.record_token_usage() treats a None usage as a
    no-op rather than guessing.
    """

    message: Dict[str, Any]
    usage: Optional[Dict[str, int]]


class LLMProvider(ABC):
    """
    Abstraction over any chat-completion backend.

    Every provider is expected to expose a `model: str` instance
    attribute (which model it's actually configured for) - used for
    logging and Phase 7's per-model token usage tracking, not enforced
    here since a plain attribute isn't something ABC can require.

    Both chat() (the old CypherAgent JSON-envelope pipeline,
    app/agent/core.py) and chat_with_tools() (the native tool-calling
    pipeline, app/agent/native_tool_agent.py) are part of this one
    interface so either pipeline can use either provider without a
    special case - Phase 7 added chat_with_tools() to CloudLLMProvider
    for exactly this reason (previously only LocalLLMProvider had it).
    """

    @abstractmethod
    async def chat(self, messages: List[ChatMessage], temperature: float = 0.3) -> str:
        """Send a list of chat messages, return the assistant's reply text.

        Raises LLMUnavailableError if the backend cannot be reached.
        """
        raise NotImplementedError

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.2,
    ) -> ChatWithToolsResponse:
        """Send a chat request using OpenAI-format function-calling (the
        `tools`/`tool_choice` request fields, `tool_calls` in the
        response message) - see app/agent/native_tool_agent.py.

        Raises LLMUnavailableError if the backend cannot be reached.

        Deliberately NOT an @abstractmethod: only providers actually
        used by the native tool-calling pipeline (LocalLLMProvider,
        CloudLLMProvider) need to implement it, and making it abstract
        would force every LLMProvider test double - including the OLD
        /api/messages pipeline's, which only ever calls chat() - to
        implement a method it never uses. The default here raises
        clearly instead, same effect for anything that does call it
        unimplemented.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support tool-calling.")

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the backend is reachable right now."""
        raise NotImplementedError
