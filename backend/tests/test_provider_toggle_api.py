"""
API-level tests for the Phase 7 provider toggle: switching via
POST /api/settings/provider actually changes which provider
/api/tool-chat uses for the next request - a real behavioral check, not
just "the setting got saved". app.api.tool_chat.get_provider_by_name is
mocked so this never makes a real network call to either Ollama or a
cloud API; get_active_provider() (the thing under test) is real,
persisted through the same SQLite session the `client` fixture uses.
"""
from __future__ import annotations

from tests.llm_doubles import ChatWithToolsResponse, OneShotLLM


class FakeToolCallingLLM(OneShotLLM):
    def __init__(self, model: str = "fake-model"):
        self.model = model

    async def chat_with_tools(self, messages, tools, temperature=0.2):
        return ChatWithToolsResponse(message={"role": "assistant", "content": "Hallo."}, usage=None)


def _patch_provider_factory(monkeypatch):
    monkeypatch.setattr("app.api.tool_chat.get_provider_by_name", lambda name, settings=None: FakeToolCallingLLM())


def _enable_cloud_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("CLOUD_LLM_API_KEY", "sk-test-key")
    monkeypatch.setenv("CLOUD_LLM_ENABLED", "true")
    get_settings.cache_clear()


def test_tool_chat_defaults_to_local_provider(client, user_headers, monkeypatch):
    _patch_provider_factory(monkeypatch)

    resp = client.post("/api/tool-chat", json={"message": "hallo"}, headers=user_headers)

    assert resp.status_code == 200
    assert resp.json()["provider"] == "local"


def test_toggling_to_cloud_actually_switches_the_provider_used(client, user_headers, monkeypatch):
    _patch_provider_factory(monkeypatch)
    _enable_cloud_key(monkeypatch)
    try:
        toggle_resp = client.post("/api/settings/provider", json={"provider": "cloud"}, headers=user_headers)
        assert toggle_resp.status_code == 200
        assert toggle_resp.json()["provider"] == "cloud"

        chat_resp = client.post("/api/tool-chat", json={"message": "hallo"}, headers=user_headers)
        assert chat_resp.json()["provider"] == "cloud"

        # And it stays that way for a subsequent request in the same
        # session - a genuine session-wide toggle, not per-request.
        chat_resp_2 = client.post("/api/tool-chat", json={"message": "nochmal"}, headers=user_headers)
        assert chat_resp_2.json()["provider"] == "cloud"
    finally:
        from app.config import get_settings

        get_settings.cache_clear()


def test_switching_back_to_local_works(client, user_headers, monkeypatch):
    _patch_provider_factory(monkeypatch)
    _enable_cloud_key(monkeypatch)
    try:
        client.post("/api/settings/provider", json={"provider": "cloud"}, headers=user_headers)
        client.post("/api/settings/provider", json={"provider": "local"}, headers=user_headers)

        resp = client.post("/api/tool-chat", json={"message": "hallo"}, headers=user_headers)
        assert resp.json()["provider"] == "local"
    finally:
        from app.config import get_settings

        get_settings.cache_clear()


def test_switching_to_cloud_without_an_api_key_is_rejected(client, user_headers, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("CLOUD_LLM_API_KEY", "")
    monkeypatch.setenv("CLOUD_LLM_ENABLED", "true")
    get_settings.cache_clear()
    try:
        resp = client.post("/api/settings/provider", json={"provider": "cloud"}, headers=user_headers)
        assert resp.status_code == 400

        # The rejected switch must not have taken effect.
        status_resp = client.get("/api/settings/provider", headers=user_headers)
        assert status_resp.json()["provider"] == "local"
    finally:
        get_settings.cache_clear()


def test_provider_endpoint_rejects_an_unknown_provider_name(client, user_headers):
    resp = client.post("/api/settings/provider", json={"provider": "quantum"}, headers=user_headers)
    assert resp.status_code == 400


def test_get_provider_reports_cloud_configured_flag(client, user_headers, monkeypatch):
    _enable_cloud_key(monkeypatch)
    try:
        resp = client.get("/api/settings/provider", headers=user_headers)
        assert resp.json()["cloud_configured"] is True
    finally:
        from app.config import get_settings

        get_settings.cache_clear()


def test_provider_endpoints_require_auth(client):
    assert client.get("/api/settings/provider").status_code == 401
    assert client.post("/api/settings/provider", json={"provider": "cloud"}).status_code == 401
