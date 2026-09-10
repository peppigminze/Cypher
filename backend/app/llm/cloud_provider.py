"""
Optional cloud LLM fallback (project spec sections 8/9).

Speaks the OpenAI-compatible chat-completions format, so it works with
the real OpenAI API as well as any compatible provider by pointing
CLOUD_LLM_BASE_URL elsewhere. Never constructed unless CLOUD_LLM_ENABLED
is true AND an API key is configured - see
app/llm/factory.py::get_cloud_llm_provider(). The API key always comes
from configuration (.env), never hardcoded.
"""
from __future__ import annotations

import logging
from typing import List

import httpx

from app.llm.base import ChatMessage, LLMProvider, LLMUnavailableError

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
