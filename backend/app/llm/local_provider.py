from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.llm.base import ChatMessage, LLMProvider, LLMUnavailableError

logger = logging.getLogger("cypher.llm.local")


class LocalLLMProvider(LLMProvider):
    """
    Talks to a local, OpenAI-compatible chat completion endpoint.

    This works out of the box with Ollama (>= 0.1.x), which exposes
    POST {base_url}/v1/chat/completions, as well as any other local
    runtime that implements the same contract (e.g. LM Studio,
    llama.cpp server in OpenAI-compat mode).

    No API key is required or sent.
    """

    def __init__(self, base_url: str, model: str, timeout_seconds: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def chat(self, messages: List[ChatMessage], temperature: float = 0.3) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            logger.error("Local LLM connection failed: %s", exc)
            raise LLMUnavailableError("Local LLM is unavailable.") from exc
        except httpx.TimeoutException as exc:
            logger.error("Local LLM request timed out: %s", exc)
            raise LLMUnavailableError("Local LLM is unavailable.") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("Local LLM returned an error status: %s", exc)
            raise LLMUnavailableError("Local LLM is unavailable.") from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected local LLM response shape: %s", data)
            raise LLMUnavailableError("Local LLM returned an unexpected response.") from exc

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """Send a chat request using the OpenAI "tools" function-calling
        format (https://platform.openai.com/docs/guides/function-calling),
        which Ollama implements natively for tool-capable models (e.g.
        Qwen2.5) - no prompt hacking or output regex parsing needed.

        Returns the raw assistant *message* object (not just its text),
        since that's the only way to see whether the model chose to call
        a tool: {"role": "assistant", "content": ..., "tool_calls": [...]}.
        `tool_calls` is absent/empty when the model answered directly.
        """
        url = f"{self.base_url}/v1/chat/completions"
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
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            logger.error("Local LLM connection failed: %s", exc)
            raise LLMUnavailableError("Local LLM is unavailable.") from exc
        except httpx.TimeoutException as exc:
            logger.error("Local LLM request timed out: %s", exc)
            raise LLMUnavailableError("Local LLM is unavailable.") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("Local LLM returned an error status: %s", exc)
            raise LLMUnavailableError("Local LLM is unavailable.") from exc

        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected local LLM response shape: %s", data)
            raise LLMUnavailableError("Local LLM returned an unexpected response.") from exc

    async def health_check(self) -> bool:
        url = f"{self.base_url}/v1/models"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                return resp.status_code < 500
        except httpx.HTTPError:
            return False
