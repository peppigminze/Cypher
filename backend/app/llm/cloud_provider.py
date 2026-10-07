"""
Cloud LLM provider - OpenAI's chat-completions API by default (Phase
0's automatic-fallback scaffold, project spec sections 8/9), and since
Phase 7 also the manually-selected provider for the native tool-calling
pipeline (app/agent/native_tool_agent.py, app/api/tool_chat.py - see
app/settings_store.py for the runtime local/cloud toggle).

Speaks the OpenAI-compatible chat-completions format, so it works with
the real OpenAI API as well as any compatible provider by pointing
CLOUD_LLM_BASE_URL elsewhere. The API key always comes from
configuration (.env), never hardcoded, and is never logged - every
error path below logs only the exception/response, never self.api_key
or self._headers().

chat_with_tools() is a thin adapter, not a translation layer: OpenAI's
API accepts the exact same `tools`/`tool_choice` request shape and
returns the same `tool_calls` response shape Ollama's OpenAI-compat
endpoint already uses (see LocalLLMProvider.chat_with_tools) - there is
no per-provider format difference to bridge here. A provider whose
native tool-calling format actually differs (e.g. Anthropic's
content-block `tool_use`) would need a real translation layer instead
of this pattern.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from app.llm.base import ChatMessage, ChatWithToolsResponse, LLMProvider, LLMUnavailableError

logger = logging.getLogger("cypher.llm.cloud")


class CloudLLMProvider(LLMProvider):
    def __init__(self, base_url: str, model: str, api_key: str, timeout_seconds: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def chat(self, messages: List[ChatMessage], temperature: float = 0.3) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {"model": self.model, "messages": messages, "temperature": temperature, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise LLMUnavailableError("Cloud LLM is unavailable.") from exc
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError("Cloud LLM is unavailable.") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("Cloud LLM returned an error status: %s", exc)
            raise LLMUnavailableError("Cloud LLM is unavailable.") from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected cloud LLM response shape: %s", data)
            raise LLMUnavailableError("Cloud LLM returned an unexpected response.") from exc

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.2,
    ) -> ChatWithToolsResponse:
        """Thin adapter, not a translation layer - see the module
        docstring for why OpenAI needs no format bridging here. Mirrors
        LocalLLMProvider.chat_with_tools() almost exactly; the only
        differences are the URL (no "/v1" suffix - CLOUD_LLM_BASE_URL
        already includes it) and the Authorization header.
        """
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            "tools": tools,
            "tool_choice": "auto",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            logger.error("Cloud LLM connection failed: %s", exc)
            raise LLMUnavailableError("Cloud LLM is unavailable.") from exc
        except httpx.TimeoutException as exc:
            logger.error("Cloud LLM request timed out: %s", exc)
            raise LLMUnavailableError("Cloud LLM is unavailable.") from exc
        except httpx.HTTPStatusError as exc:
            # exc's default __str__ is "<method> <url> ... <status>" -
            # no headers, so the API key is never at risk of landing in
            # this log line.
            logger.error("Cloud LLM returned an error status: %s", exc)
            raise LLMUnavailableError("Cloud LLM is unavailable.") from exc

        usage = data.get("usage")
        if usage:
            logger.info(
                "TOKEN USAGE -> provider=cloud model=%s prompt=%s completion=%s total=%s",
                self.model,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
            )

        try:
            return ChatWithToolsResponse(message=data["choices"][0]["message"], usage=usage)
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected cloud LLM response shape: %s", data)
            raise LLMUnavailableError("Cloud LLM returned an unexpected response.") from exc

    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        url = f"{self.base_url}/models"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=self._headers())
                return resp.status_code < 500
        except httpx.HTTPError:
            return False
