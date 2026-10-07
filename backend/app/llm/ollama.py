"""
Ollama provider using the NATIVE API (/api/chat), not the OpenAI-compat
endpoint the old LocalLLMProvider used.

Only the native API lets Cypher control what matters on this hardware
(see docs/overhaul/AUDIT.md):
    options.num_ctx  - context size; otherwise Ollama derives 4096 from free VRAM
    keep_alive       - how long the model stays loaded (default 5m -> cold starts)
    think            - Qwen 3.5 "thinks" by default, 5-30 s per answer
and it streams tool calls as complete objects together with timing fields.

Wire format differences handled here:
    - tool-call arguments are JSON objects (OpenAI: JSON strings)
    - tool results are {"role": "tool", "tool_name": ..., "content": ...}
      (OpenAI: "tool_call_id")
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.core.metrics import metrics
from app.llm.base import (
    ChatMessage,
    ChatWithToolsResponse,
    LLMEvent,
    LLMProvider,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelNotFoundError,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    Usage,
    collect,
    new_tool_call_id,
    parse_tool_arguments,
)
from app.llm.http import shared_client

logger = logging.getLogger("cypher.llm.ollama")


def to_ollama_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Cypher's canonical (OpenAI-style) messages to Ollama's format."""
    call_names: Dict[str, str] = {}
    out: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "assistant" and msg.get("tool_calls"):
            calls = []
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                args, _raw = parse_tool_arguments(fn.get("arguments"))
                if tc.get("id"):
                    call_names[tc["id"]] = fn.get("name", "")
                calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})
            out.append({"role": "assistant", "content": content, "tool_calls": calls})
        elif role == "tool":
            name = msg.get("tool_name") or msg.get("name") or call_names.get(msg.get("tool_call_id", ""), "")
            out.append({"role": "tool", "content": content if isinstance(content, str) else json.dumps(content), "tool_name": name})
        else:
            entry: Dict[str, Any] = {"role": role, "content": content}
            if msg.get("images"):
                entry["images"] = msg["images"]
            out.append(entry)
    return out


class OllamaProvider(LLMProvider):
    """Talks to a local Ollama server. No API key."""

    name = "local"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        *,
        connect_timeout_seconds: float = 3.0,
        num_ctx: int = 8192,
        keep_alive: str = "30m",
        think: bool = False,
        temperature: float = 0.3,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.think = think
        self.temperature = temperature
        # Tests inject an httpx.MockTransport; production shares a pooled client.
        self._transport = transport

    # ------------------------------------------------------------ plumbing
    def _timeout(self) -> httpx.Timeout:
        # `read` bounds the gap between streamed chunks, not the whole answer.
        return httpx.Timeout(connect=self.connect_timeout_seconds, read=self.timeout_seconds, write=10.0, pool=5.0)

    def _client(self) -> httpx.AsyncClient:
        if self._transport is not None:
            return httpx.AsyncClient(transport=self._transport, timeout=self._timeout())
        return shared_client(f"ollama:{self.base_url}", lambda: httpx.AsyncClient(timeout=self._timeout(), limits=httpx.Limits(max_keepalive_connections=4)))

    def _payload(self, messages, tools, temperature, max_tokens, stream: bool) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "num_ctx": self.num_ctx,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": to_ollama_messages(messages),
            "stream": stream,
            "keep_alive": self.keep_alive,
            "options": options,
            "think": self.think,
        }
        if tools:
            payload["tools"] = tools
        return payload

    def _unavailable(self, message: str) -> LLMUnavailableError:
        return LLMUnavailableError(message, provider="local", base_url=self.base_url)

    # ------------------------------------------------------------ streaming
    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[LLMEvent]:
        payload = self._payload(messages, tools, temperature, max_tokens, stream=True)
        client = self._client()
        started = time.perf_counter()
        first_token_at: Optional[float] = None
        try:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload, timeout=self._timeout()) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise self._http_error(resp.status_code, body, payload)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise LLMResponseError(f"Unparseable stream line: {line[:200]!r}", base_url=self.base_url) from exc
                    if chunk.get("error"):
                        raise LLMResponseError(str(chunk["error"]), base_url=self.base_url)
                    message = chunk.get("message") or {}
                    text = message.get("content") or ""
                    calls = message.get("tool_calls") or []
                    if first_token_at is None and (text or calls):
                        first_token_at = time.perf_counter()
                        metrics.record("llm_ttft", first_token_at - started)
                    if text:
                        yield TextDelta(text)
                    for tc in calls:
                        fn = tc.get("function") or {}
                        args, raw = parse_tool_arguments(fn.get("arguments"))
                        yield ToolCallEvent(ToolCall(id=tc.get("id") or new_tool_call_id(), name=fn.get("name", ""), arguments=args, raw_arguments=raw))
                    if chunk.get("done"):
                        metrics.record("llm_total", time.perf_counter() - started)
                        yield StreamDone(usage=self._usage(chunk), done_reason=chunk.get("done_reason") or "stop", timings=self._timings(chunk))
                        return
        except httpx.ConnectError as exc:
            logger.warning("Ollama not reachable at %s: %s", self.base_url, exc)
            raise self._unavailable("Local LLM is unavailable.") from exc
        except httpx.TimeoutException as exc:
            logger.warning("Ollama timed out after %.0fs", self.timeout_seconds)
            raise LLMTimeoutError(self.timeout_seconds, base_url=self.base_url) from exc
        except httpx.HTTPError as exc:
            raise self._unavailable(f"Local LLM request failed: {exc}") from exc
        # Stream ended without a done chunk - Ollama crashed mid-answer.
        raise LLMResponseError("Stream ended unexpectedly (Ollama runner stopped?).", base_url=self.base_url)

    def _http_error(self, status: int, body: str, payload: Dict[str, Any]) -> LLMUnavailableError:
        try:
            detail = json.loads(body).get("error", body)
        except (json.JSONDecodeError, AttributeError):
            detail = body
        logger.warning("Ollama returned HTTP %s: %s", status, detail)
        if status == 404 or "not found" in str(detail).lower():
            return ModelNotFoundError(self.model, base_url=self.base_url)
        return LLMResponseError(f"Ollama HTTP {status}: {detail}", base_url=self.base_url)

    @staticmethod
    def _usage(chunk: Dict[str, Any]) -> Optional[Usage]:
        if "prompt_eval_count" not in chunk and "eval_count" not in chunk:
            return None
        return Usage(
            prompt_tokens=int(chunk.get("prompt_eval_count") or 0),
            completion_tokens=int(chunk.get("eval_count") or 0),
            cached_prompt_tokens=int(chunk.get("prompt_eval_cached_count") or 0),
        )

    @staticmethod
    def _timings(chunk: Dict[str, Any]) -> Dict[str, float]:
        out = {}
        for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
            if chunk.get(key):
                out[key.removesuffix("_duration")] = chunk[key] / 1e9
        return out

    # ------------------------------------------------------------ legacy API
    async def chat(self, messages: List[ChatMessage], temperature: float = 0.3) -> str:
        response = await collect(self.stream_chat(list(messages), None, temperature=temperature))
        return response.content

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.2,
    ) -> ChatWithToolsResponse:
        response = await collect(self.stream_chat(messages, tools, temperature=temperature))
        usage = response.usage.as_openai_dict() if response.usage else None
        return ChatWithToolsResponse(message=response.to_openai_message(), usage=usage)

    async def health_check(self) -> bool:
        try:
            resp = await self._client().get(f"{self.base_url}/api/version", timeout=httpx.Timeout(3.0))
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


class OllamaAdmin:
    """Model management calls (version, installed/loaded models, load/unload)."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 5.0, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        if self._transport is not None:
            return httpx.AsyncClient(transport=self._transport, timeout=self.timeout_seconds)
        return shared_client(f"ollama-admin:{self.base_url}", lambda: httpx.AsyncClient(timeout=self.timeout_seconds))

    async def _get(self, path: str) -> Dict[str, Any]:
        try:
            resp = await self._client().get(f"{self.base_url}{path}", timeout=self.timeout_seconds)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as exc:
            raise LLMUnavailableError("Ollama not reachable.", base_url=self.base_url) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(self.timeout_seconds, base_url=self.base_url) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMResponseError(f"Ollama {path} failed: {exc}", base_url=self.base_url) from exc

    async def version(self) -> str:
        return str((await self._get("/api/version")).get("version", "?"))

    async def installed_models(self) -> List[str]:
        return [m.get("name", "") for m in (await self._get("/api/tags")).get("models", [])]

    async def loaded_models(self) -> List[Dict[str, Any]]:
        return list((await self._get("/api/ps")).get("models", []))

    async def unload(self, model: str) -> None:
        try:
            await self._client().post(f"{self.base_url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30.0)
        except httpx.HTTPError as exc:
            logger.warning("Could not unload %s: %s", model, exc)


def model_matches(name: str, wanted: str) -> bool:
    """Ollama lists 'qwen3.5:9b' but also accepts 'qwen3.5' for ':latest'."""
    if name == wanted:
        return True
    if ":" not in wanted:
        return name == f"{wanted}:latest"
    return False
