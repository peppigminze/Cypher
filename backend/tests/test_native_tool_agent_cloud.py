"""
End-to-end test of the native tool-calling pipeline running against the
CLOUD provider (Phase 7) - the real NativeToolCallingAgent loop,
ToolExecutor, and ToolRegistry, with a real get_current_time tool call.
Only the outbound HTTP call to the cloud API is faked (via
httpx.AsyncClient, same pattern as test_web_tools.py/test_cloud_provider.py).

Deliberately mocked rather than a live OpenAI call: no test should
require a real API key, cost real money, or depend on network access/
model availability to pass in CI - the same reasoning
test_cloud_fallback.py already applies to the OLD pipeline's cloud
fallback tests. This still exercises the real adapter code
(CloudLLMProvider.chat_with_tools), the real agent loop, and real tool
execution/DB usage recording - only the network boundary is faked.
"""
from __future__ import annotations

import pytest

from app.agent.native_tool_agent import NativeToolCallingAgent
from app.database.db import Base, SessionLocal, engine
from app.llm.cloud_provider import CloudLLMProvider
from app.settings_store import get_usage_summary
from app.tools.defaults import build_default_registry


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class ScriptedCloudClient:
    """Step 1: the model asks for get_current_time. Step 2: it gives a
    final answer using that tool's result. Each step reports its own
    token usage, so the test can verify both calls got persisted."""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        ScriptedCloudClient.calls.append(json)
        if len(ScriptedCloudClient.calls) == 1:
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "get_current_time", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
                }
            )
        return FakeResponse(
            {
                "choices": [{"message": {"role": "assistant", "content": "Es ist gerade 12:00 Uhr UTC."}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 15, "total_tokens": 95},
            }
        )


async def test_cloud_tool_calling_end_to_end(monkeypatch, db_session):
    ScriptedCloudClient.calls = []
    monkeypatch.setattr("app.llm.cloud_provider.httpx.AsyncClient", ScriptedCloudClient)

    llm = CloudLLMProvider(base_url="https://api.openai.com/v1", model="gpt-5.6-luna", api_key="sk-test-key")
    registry = build_default_registry()
    agent = NativeToolCallingAgent(llm=llm, tools=registry, tool_names=["get_current_time"], provider_name="cloud")

    result = await agent.run(db_session, "Wie spät ist es?")

    assert result.reply == "Es ist gerade 12:00 Uhr UTC."
    assert result.pending_confirmation is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_current_time"
    assert result.tool_calls[0].success is True
    assert len(ScriptedCloudClient.calls) == 2  # one to decide the tool, one for the final answer

    # Both LLM calls' token usage landed in the persistent counter under
    # "cloud" - not "local", and not double-counted or dropped.
    summary = get_usage_summary(db_session)
    cloud_usage = summary["today"]["by_provider"]["cloud"]
    assert cloud_usage["total_tokens"] == 60 + 95
    assert cloud_usage["request_count"] == 2
    assert "local" not in summary["today"]["by_provider"]


async def test_cloud_tool_calling_pauses_for_a_confirm_required_tool(monkeypatch, db_session):
    """write_file is CONFIRM_REQUIRED - the approval gate must apply
    identically regardless of which provider is active."""

    class SingleCallClient(ScriptedCloudClient):
        async def post(self, url, json, headers):
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "write_file",
                                            "arguments": '{"path": "notiz.txt", "content": "hi"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
                }
            )

    monkeypatch.setattr("app.llm.cloud_provider.httpx.AsyncClient", SingleCallClient)

    llm = CloudLLMProvider(base_url="https://api.openai.com/v1", model="gpt-5.6-luna", api_key="sk-test-key")
    registry = build_default_registry()
    agent = NativeToolCallingAgent(llm=llm, tools=registry, tool_names=["write_file"], provider_name="cloud")

    result = await agent.run(db_session, "Erstelle notiz.txt mit 'hi'.")

    assert result.pending_confirmation is not None
    assert result.pending_confirmation.name == "write_file"
    assert result.tool_calls == []  # nothing executed yet - still awaiting approval
