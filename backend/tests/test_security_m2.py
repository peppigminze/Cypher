"""M2 security: generated tokens, local-UI bootstrap, origin checks, rate limiting."""
from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.security import tokens
from app.security.origin import is_trusted_local_browser_request, websocket_origin_ok
from app.security.rate_limit import AUTH_FAILURE_LIMIT, RateLimiter


# ------------------------------------------------------------------ tokens
def test_placeholder_tokens_are_replaced_by_generated_ones(tmp_path):
    s = Settings(_env_file=None, DATA_DIR=str(tmp_path), USER_TOKEN="change-me-user-token", AGENT_TOKEN="")
    t = tokens.get_tokens(s)
    assert t.user_source == "generated" and t.agent_source == "generated"
    assert len(t.user_token) >= 40 and t.user_token != t.agent_token
    stored = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert stored["user_token"] == t.user_token


def test_generated_token_is_stable_across_restarts(tmp_path):
    s = Settings(_env_file=None, DATA_DIR=str(tmp_path), USER_TOKEN="change-me-user-token")
    first = tokens.get_tokens(s).user_token
    tokens.reset_cache()
    assert tokens.get_tokens(s).user_token == first


def test_env_token_wins_and_writes_no_file(tmp_path):
    s = Settings(_env_file=None, DATA_DIR=str(tmp_path), USER_TOKEN="u" * 40, AGENT_TOKEN="a" * 40)
    t = tokens.get_tokens(s)
    assert t.user_token == "u" * 40 and t.user_source == "env"
    assert not (tmp_path / "secrets.json").exists()


def test_placeholder_token_is_never_accepted(client):
    resp = client.get("/api/settings/provider", headers={"Authorization": "Bearer change-me-user-token"})
    assert resp.status_code == 401


# ------------------------------------------------------------------ local session bootstrap
def test_local_ui_gets_token_without_typing_it(client, user_headers):
    resp = client.get("/api/auth/session", headers={"Sec-Fetch-Site": "same-origin"})
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert client.get("/api/auth/check", headers={"Authorization": f"Bearer {token}"}).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},  # another website in the user's browser
        {"Sec-Fetch-Site": "cross-site"},
        {"Host": "evil.example:8000"},  # DNS rebinding: name resolves to 127.0.0.1
        {"Origin": "http://evil.example", "Host": "127.0.0.1:8000"},
    ],
)
def test_session_refused_for_cross_site_or_rebinding(client, headers):
    resp = client.get("/api/auth/session", headers=headers)
    assert resp.status_code == 403
    assert "Nur direkt am PC" in resp.json()["detail"]


def test_session_refused_for_remote_peer():
    s = Settings(_env_file=None)
    assert not is_trusted_local_browser_request("100.64.1.2", {"host": "127.0.0.1:8000"}, s)
    assert not is_trusted_local_browser_request("192.168.1.20", {"host": "localhost:8000"}, s)
    assert is_trusted_local_browser_request("127.0.0.1", {"host": "127.0.0.1:8000", "origin": "http://localhost:5173", "sec-fetch-site": "same-site"}, s)


def test_websocket_origin_check():
    s = Settings(_env_file=None, CORS_ORIGINS="http://localhost:5173")
    assert websocket_origin_ok({"origin": "http://localhost:5173"}, s)
    assert websocket_origin_ok({"origin": "http://127.0.0.1:8000", "host": "127.0.0.1:8000"}, s)  # served by the backend
    assert not websocket_origin_ok({"origin": "https://evil.example", "host": "127.0.0.1:8000"}, s)
    assert websocket_origin_ok({}, s)  # non-browser clients must still authenticate


# ------------------------------------------------------------------ rate limiting
def test_token_bucket_refills_over_time():
    rl = RateLimiter()
    allowed = [rl.allow("chat", "c", now=0.0)[0] for _ in range(30)]
    assert all(allowed)
    ok, retry = rl.allow("chat", "c", now=0.0)
    assert not ok and retry > 0
    assert rl.allow("chat", "c", now=3.0)[0]  # 30/min -> one token every 2 s


def test_repeated_auth_failures_lock_the_address_out(client, user_headers):
    for _ in range(AUTH_FAILURE_LIMIT):
        assert client.get("/api/auth/check", headers={"Authorization": "Bearer wrong"}).status_code == 401
    # Even the correct token is refused while locked out.
    resp = client.get("/api/auth/check", headers=user_headers)
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0


def test_chat_endpoint_is_rate_limited(client, user_headers, monkeypatch):
    from app.security import rate_limit

    monkeypatch.setitem(rate_limit.LIMITS, "chat", rate_limit.Limit(2, 60))
    codes = [client.post("/api/tool-chat", json={"message": "hi"}, headers=user_headers).status_code for _ in range(3)]
    assert codes[-1] == 429
    assert codes[0] != 429
