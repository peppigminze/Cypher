"""
Cloud provider (OpenAI Responses API) - through the real `openai` SDK
against a simulated server (httpx MockTransport). No real key, no network,
no cost; but the SDK's own request building and SSE parsing are exercised.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

import httpx
import openai
import pytest

from app.agent.engine import ConversationEngine, ConversationStore, Sentence, TurnDone, TurnFailed
from app.core import errors
from app.llm import cloud_provider
from app.llm.base import LLMResponseError, LLMUnavailableError, StreamDone, TextDelta, ToolCallEvent, collect
from app.llm.cloud_provider import CloudAuthError, CloudLLMProvider, CloudRateLimitError, translate_messages, translate_tools
from app.llm.pricing import cost_usd
from app.tools.base import RiskTier, Tool, ToolResult
from app.tools.registry import ToolRegistry

FAKE_KEY = "sk-should-never-appear-in-a-log-or-error"


def sse(*events: Dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def completed(input_tokens=100, cached=40, output_tokens=20) -> Dict[str, Any]:
    return {"type": "response.completed", "sequence_number": 99, "response": {
        "id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-6-luna", "status": "completed", "output": [],
        "usage": {"input_tokens": input_tokens, "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
                  "output_tokens": output_tokens, "output_tokens_details": {"reasoning_tokens": 5}, "total_tokens": input_tokens + output_tokens}}}


def text_delta(text: str, seq: int = 1) -> Dict[str, Any]:
    return {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": text, "sequence_number": seq, "logprobs": []}


def function_call(name: str, args: Dict[str, Any], call_id: str = "call_1") -> Dict[str, Any]:
    return {"type": "response.output_item.done", "output_index": 1, "sequence_number": 5,
            "item": {"type": "function_call", "id": "fc_1", "call_id": call_id, "name": name, "arguments": json.dumps(args), "status": "completed"}}


def reasoning_item() -> Dict[str, Any]:
    return {"type": "response.output_item.done", "output_index": 0, "sequence_number": 4,
            "item": {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "gAAAA-opaque"}}


class Server:
    """Records requests; answers each with the next scripted response."""

    def __init__(self, *responses: Callable[[httpx.Request], httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: List[Dict[str, Any]] = []
        self.headers: List[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        return self.responses.pop(0)(request)


def stream_of(*events) -> Callable[[httpx.Request], httpx.Response]:
    return lambda req: httpx.Response(200, content=sse(*events), headers={"content-type": "text/event-stream"})


def status(code: int, message: str = "nope") -> Callable[[httpx.Request], httpx.Response]:
    return lambda req: httpx.Response(code, json={"error": {"message": message, "type": "x", "code": None}})


def provider(server: Server, effort: str = "low") -> CloudLLMProvider:
    client = openai.AsyncOpenAI(api_key=FAKE_KEY, base_url="https://api.openai.com/v1", max_retries=0,
                                http_client=httpx.AsyncClient(transport=httpx.MockTransport(server)))
    return CloudLLMProvider("https://api.openai.com/v1", "gpt-6-luna", FAKE_KEY, reasoning_effort=effort, client=client)


@pytest.fixture(autouse=True)
def _clear_reasoning_cache():
    cloud_provider._REASONING_FOR_CALL.clear()
    yield


# ------------------------------------------------------------------ request shape
async def test_request_uses_responses_api_without_storing_anything():
    server = Server(stream_of(text_delta("Hallo"), completed()))
    tools = [{"type": "function", "function": {"name": "get_weather", "description": "Wetter", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}}}]
    await collect(provider(server).stream_chat([{"role": "system", "content": "Du bist Cypher."}, {"role": "user", "content": "Hi"}], tools))
    body = server.requests[0]
    assert body["model"] == "gpt-6-luna" and body["stream"] is True
    assert body["store"] is False and body["include"] == ["reasoning.encrypted_content"]
    assert body["instructions"] == "Du bist Cypher."
    assert body["input"] == [{"role": "user", "content": "Hi"}]
    assert body["reasoning"] == {"effort": "low"}
    assert body["tools"] == [{"type": "function", "name": "get_weather", "description": "Wetter", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}, "strict": False}]
    assert "temperature" not in body
    assert server.headers[0]["authorization"] == f"Bearer {FAKE_KEY}"


async def test_streams_text_and_reports_usage_with_cached_tokens():
    server = Server(stream_of(text_delta("Hallo ", 1), text_delta("Silvan.", 2), completed(100, 40, 20)))
    events = [e async for e in provider(server).stream_chat([{"role": "user", "content": "Hi"}])]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hallo ", "Silvan."]
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert (done.usage.prompt_tokens, done.usage.cached_prompt_tokens, done.usage.completion_tokens) == (100, 40, 20)


async def test_function_call_round_trip_sends_reasoning_back():
    server = Server(stream_of(reasoning_item(), function_call("get_weather", {"location": "Bern"}), completed()), stream_of(text_delta("Regen."), completed()))
    llm = provider(server)
    first = [e async for e in llm.stream_chat([{"role": "user", "content": "Wetter Bern?"}])]
    call = next(e for e in first if isinstance(e, ToolCallEvent)).call
    assert (call.id, call.name, call.arguments) == ("call_1", "get_weather", {"location": "Bern"})
    history = [
        {"role": "user", "content": "Wetter Bern?"},
        {"role": "assistant", "content": "", "tool_calls": [call.to_openai()]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"success": true, "data": "Regen"}'},
    ]
    await collect(llm.stream_chat(history))
    items = server.requests[1]["input"]
    assert [i.get("type", i.get("role")) for i in items] == ["user", "reasoning", "function_call", "function_call_output"]
    assert items[1]["encrypted_content"] == "gAAAA-opaque"
    assert items[2] == {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": '{"location": "Bern"}'}
    assert items[3] == {"type": "function_call_output", "call_id": "call_1", "output": '{"success": true, "data": "Regen"}'}


# ------------------------------------------------------------------ errors
@pytest.mark.parametrize(
    "code, exc_type, catalog",
    [(401, CloudAuthError, "cloud_auth_failed"), (429, CloudRateLimitError, "cloud_rate_limited"), (400, LLMResponseError, "llm_bad_response")],
)
async def test_http_errors_become_german_catalog_errors(code, exc_type, catalog):
    server = Server(status(code, f"bad key {FAKE_KEY}"))
    with pytest.raises(exc_type) as exc:
        await collect(provider(server).stream_chat([{"role": "user", "content": "Hi"}]))
    err = errors.from_exception(exc.value)
    assert err.code == catalog
    assert FAKE_KEY not in str(exc.value) and FAKE_KEY not in err.message and FAKE_KEY not in (err.hint or "")


async def test_connection_error_is_cloud_unavailable():
    def boom(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(LLMUnavailableError) as exc:
        await collect(provider(Server(boom)).stream_chat([{"role": "user", "content": "Hi"}]))
    assert errors.from_exception(exc.value).code == "cloud_unavailable"


async def test_failed_response_event_raises():
    failed = {"type": "response.failed", "sequence_number": 3, "response": {"id": "r", "object": "response", "created_at": 0, "model": "gpt-6-luna", "status": "failed", "output": [], "error": {"code": "server_error", "message": "boom"}}}
    with pytest.raises(LLMResponseError):
        await collect(provider(Server(stream_of(failed))).stream_chat([{"role": "user", "content": "Hi"}]))


# ------------------------------------------------------------------ translation + cost
def test_translation_helpers():
    instructions, items = translate_messages([{"role": "system", "content": "A"}, {"role": "system", "content": "B"}, {"role": "assistant", "content": "Hallo"}])
    assert instructions == "A\n\nB" and items == [{"role": "assistant", "content": "Hallo"}]
    assert translate_tools([{"type": "function", "function": {"name": "x"}}])[0]["parameters"] == {"type": "object", "properties": {}}


def test_cost_from_official_prices():
    # gpt-6-luna: $0.10 input, $0.01 cached, $0.50 output per 1M (checked 2026-10-08)
    assert cost_usd("gpt-6-luna", 1_000_000, 400_000, 100_000) == pytest.approx(0.06 + 0.004 + 0.05)
    assert cost_usd("unknown-model", 10, 0, 10) is None


# ------------------------------------------------------------------ engine end to end
class WeatherTool(Tool):
    name = "get_weather"
    description = "Wetter"
    parameters = {"type": "object", "properties": {"location": {"type": "string"}}}
    risk = RiskTier.READ_ONLY

    async def execute(self, db=None, location: str = "", **_):
        return ToolResult(success=True, data={"location": location, "conditions": "Regen"})


async def test_engine_tool_loop_over_the_cloud():
    server = Server(stream_of(function_call("get_weather", {"location": "Bern"}), completed()), stream_of(text_delta("In Bern regnet es."), completed()))
    registry = ToolRegistry()
    registry.register(WeatherTool())
    engine = ConversationEngine(provider(server), registry, provider_name="cloud", store=ConversationStore(), tool_names=["get_weather"])
    events = [e async for e in engine.run_turn("c", "Wie wird das Wetter in Bern?", source="text")]
    assert isinstance(events[-1], TurnDone) and events[-1].reply == "In Bern regnet es."
    assert server.requests[1]["input"][-1]["type"] == "function_call_output"


async def test_engine_reports_rejected_key_in_german():
    engine = ConversationEngine(provider(Server(status(401))), ToolRegistry(), provider_name="cloud", store=ConversationStore(), tool_names=[])
    events = [e async for e in engine.run_turn("c", "Hallo", source="voice")]
    assert isinstance(events[-1], TurnFailed) and events[-1].error.code == "cloud_auth_failed"
    assert not any(isinstance(e, Sentence) for e in events[:-1])
