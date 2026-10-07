"""
CloudLLMProvider.chat_with_tools() tests. Mocks httpx.AsyncClient rather
than calling a real API - no test should depend on a real API key,
network access, or incur real cost. Same reasoning/pattern as
test_web_tools.py's FakeAsyncClient.
"""
from __future__ import annotations

import httpx
import pytest

from app.llm.base import LLMUnavailableError
from app.llm.cloud_provider import CloudLLMProvider

FAKE_API_KEY = "sk-should-never-appear-in-a-log-or-error"


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            raise httpx.HTTPStatusError("error", request=request, response=httpx.Response(self.status_code, request=request))

    def json(self) -> dict:
        return self._payload


class FakeAsyncClient:
    last_headers: dict | None = None
    last_json: dict | None = None

    def __init__(self, *args, response: FakeResponse | None = None, **kwargs):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        FakeAsyncClient.last_headers = headers
        FakeAsyncClient.last_json = json
        return self._response


def _patch_client(monkeypatch, response: FakeResponse):
    def factory(*args, **kwargs):
        return FakeAsyncClient(*args, response=response, **kwargs)

    monkeypatch.setattr("app.llm.cloud_provider.httpx.AsyncClient", factory)


def _provider() -> CloudLLMProvider:
    return CloudLLMProvider(base_url="https://api.openai.com/v1", model="gpt-5.6-luna", api_key=FAKE_API_KEY)


async def test_chat_with_tools_returns_message_and_usage(monkeypatch):
    _patch_client(
        monkeypatch,
        FakeResponse(
            {
                "choices": [{"message": {"role": "assistant", "content": "Erledigt."}}],
                "usage": {"prompt_tokens": 42, "completion_tokens": 8, "total_tokens": 50},
            }
        ),
    )

    response = await _provider().chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert response.message == {"role": "assistant", "content": "Erledigt."}
    assert response.usage == {"prompt_tokens": 42, "completion_tokens": 8, "total_tokens": 50}


async def test_chat_with_tools_sends_bearer_auth_header(monkeypatch):
    _patch_client(monkeypatch, FakeResponse({"choices": [{"message": {"role": "assistant", "content": "ok"}}]}))

    await _provider().chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert FakeAsyncClient.last_headers == {"Authorization": f"Bearer {FAKE_API_KEY}"}


async def test_chat_with_tools_forwards_tools_and_tool_choice(monkeypatch):
    _patch_client(monkeypatch, FakeResponse({"choices": [{"message": {"role": "assistant", "content": "ok"}}]}))
    tools = [{"type": "function", "function": {"name": "get_current_time", "parameters": {}}}]

    await _provider().chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=tools)

    assert FakeAsyncClient.last_json["tools"] == tools
    assert FakeAsyncClient.last_json["tool_choice"] == "auto"


async def test_chat_with_tools_handles_a_tool_call_response(monkeypatch):
    tool_call_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_current_time", "arguments": "{}"}}],
    }
    _patch_client(monkeypatch, FakeResponse({"choices": [{"message": tool_call_message}]}))

    response = await _provider().chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert response.message["tool_calls"][0]["function"]["name"] == "get_current_time"
    assert response.usage is None  # backend didn't report usage - must not crash or fabricate one


async def test_chat_with_tools_missing_usage_is_none_not_an_error(monkeypatch):
    _patch_client(monkeypatch, FakeResponse({"choices": [{"message": {"role": "assistant", "content": "ok"}}]}))
    response = await _provider().chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert response.usage is None


async def test_chat_with_tools_connect_error_raises_llm_unavailable(monkeypatch):
    class FailingClient(FakeAsyncClient):
        async def post(self, url, json, headers):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.llm.cloud_provider.httpx.AsyncClient", FailingClient)

    with pytest.raises(LLMUnavailableError):
        await _provider().chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])


async def test_api_key_never_appears_in_the_raised_error_message(monkeypatch):
    _patch_client(monkeypatch, FakeResponse({}, status_code=401))

    with pytest.raises(LLMUnavailableError) as exc_info:
        await _provider().chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert FAKE_API_KEY not in str(exc_info.value)
