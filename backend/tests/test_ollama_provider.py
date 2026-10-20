"""OllamaProvider against a scripted fake Ollama (httpx.MockTransport) -
no real model needed."""
from __future__ import annotations

import json

import httpx
import pytest

from app.llm.base import (
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelNotFoundError,
    StreamDone,
    TextDelta,
    ToolCallEvent,
    collect,
)
from app.llm.ollama import OllamaProvider, model_matches, to_ollama_messages


def ndjson(*chunks: dict) -> bytes:
    return ("\n".join(json.dumps(c) for c in chunks) + "\n").encode()


def provider_with(handler, **kwargs) -> OllamaProvider:
    return OllamaProvider("http://ollama.test", "qwen3.5:9b", transport=httpx.MockTransport(handler), **kwargs)


async def test_streams_text_and_reports_usage_and_timings():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"role": "assistant", "content": "Hallo "}, "done": False},
                {"message": {"role": "assistant", "content": "Silvan."}, "done": False},
                {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                 "prompt_eval_count": 12, "eval_count": 3, "prompt_eval_cached_count": 10,
                 "eval_duration": 30_000_000, "load_duration": 1_000_000},
            ),
        )

    events = [e async for e in provider_with(handler).stream_chat([{"role": "user", "content": "Hi"}])]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hallo ", "Silvan."]
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.usage.prompt_tokens == 12 and done.usage.completion_tokens == 3 and done.usage.cached_prompt_tokens == 10
    assert done.timings["eval"] == pytest.approx(0.03)


async def test_request_carries_num_ctx_keep_alive_and_think_false():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=ndjson({"message": {"content": "ok"}, "done": True}))

    provider = provider_with(handler, num_ctx=8192, keep_alive="30m", think=False, temperature=0.3)
    await collect(provider.stream_chat([{"role": "user", "content": "x"}], max_tokens=5))
    assert seen["options"]["num_ctx"] == 8192
    assert seen["options"]["num_predict"] == 5
    assert seen["keep_alive"] == "30m"
    assert seen["think"] is False
    assert seen["stream"] is True


async def test_tool_calls_are_parsed_with_object_arguments_and_get_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "set_volume", "arguments": {"level": 30}}}]}, "done": False},
                {"message": {"content": ""}, "done": True},
            ),
        )

    events = [e async for e in provider_with(handler).stream_chat([{"role": "user", "content": "leiser"}], tools=[{"type": "function"}])]
    calls = [e.call for e in events if isinstance(e, ToolCallEvent)]
    assert len(calls) == 1
    assert calls[0].name == "set_volume" and calls[0].arguments == {"level": 30}
    assert calls[0].id.startswith("call_")
    assert calls[0].arguments_valid


async def test_garbage_tool_arguments_are_flagged_not_silently_emptied():
    def handler(request):
        return httpx.Response(200, content=ndjson(
            {"message": {"tool_calls": [{"function": {"name": "x", "arguments": "{broken"}}]}, "done": False},
            {"message": {}, "done": True},
        ))

    response = await collect(provider_with(handler).stream_chat([{"role": "user", "content": "x"}]))
    assert response.tool_calls[0].arguments_valid is False
    assert response.tool_calls[0].raw_arguments == "{broken"


async def test_missing_model_raises_model_not_found():
    def handler(request):
        return httpx.Response(404, json={"error": "model 'qwen3.5:9b' not found"})

    with pytest.raises(ModelNotFoundError) as exc_info:
        await collect(provider_with(handler).stream_chat([{"role": "user", "content": "x"}]))
    assert exc_info.value.model == "qwen3.5:9b"


async def test_connection_refused_raises_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMUnavailableError) as exc_info:
        await collect(provider_with(handler).stream_chat([{"role": "user", "content": "x"}]))
    assert not isinstance(exc_info.value, (ModelNotFoundError, LLMTimeoutError))
    assert exc_info.value.provider == "local"


async def test_timeout_raises_timeout_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMTimeoutError):
        await collect(provider_with(handler, timeout_seconds=5).stream_chat([{"role": "user", "content": "x"}]))


async def test_stream_without_done_chunk_is_an_error():
    def handler(request):
        return httpx.Response(200, content=ndjson({"message": {"content": "Hal"}, "done": False}))

    with pytest.raises(LLMResponseError):
        await collect(provider_with(handler).stream_chat([{"role": "user", "content": "x"}]))


async def test_error_chunk_mid_stream_is_an_error():
    def handler(request):
        return httpx.Response(200, content=ndjson({"error": "runner crashed"}))

    with pytest.raises(LLMResponseError, match="runner crashed"):
        await collect(provider_with(handler).stream_chat([{"role": "user", "content": "x"}]))


def test_openai_style_history_is_converted_to_ollama_format():
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "leiser"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "set_volume", "arguments": '{"level": 20}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"success": true}'},
    ]
    converted = to_ollama_messages(history)
    assert converted[2] == {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "set_volume", "arguments": {"level": 20}}}]}
    assert converted[3] == {"role": "tool", "content": '{"success": true}', "tool_name": "set_volume"}


def test_model_name_matching():
    assert model_matches("qwen3.5:9b", "qwen3.5:9b")
    assert model_matches("llama3:latest", "llama3")
    assert not model_matches("qwen3.5:4b", "qwen3.5:9b")
