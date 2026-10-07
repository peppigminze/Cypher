"""
Regression tests for the security-hardening pass on app/auth.py and
app/main.py (project spec section 37).
"""
from __future__ import annotations

import pytest

from app.auth import _tokens_match
from app.main import _PLACEHOLDER_TOKENS, _check_tokens


def test_tokens_match_correct_pair():
    assert _tokens_match("abc123", "abc123") is True


def test_tokens_match_rejects_wrong_token():
    assert _tokens_match("abc123", "wrong") is False


def test_tokens_match_rejects_different_length():
    assert _tokens_match("short", "a-much-longer-token") is False


def test_valid_token_still_authenticates(client, agent_headers):
    resp = client.get("/api/sync/pending", headers=agent_headers)
    assert resp.status_code == 200


def test_wrong_token_still_rejected(client):
    resp = client.get("/api/sync/pending", headers={"Authorization": "Bearer totally-wrong"})
    assert resp.status_code == 401


class _FakeSettings:
    def __init__(self, user_token: str, agent_token: str, api_host: str = "127.0.0.1"):
        self.USER_TOKEN = user_token
        self.AGENT_TOKEN = agent_token
        self.API_HOST = api_host


STRONG = "x" * 40


def test_placeholder_tokens_are_only_reported_because_a_token_is_generated(caplog):
    """M2: placeholders are never accepted as credentials (app/security/tokens.py
    generates a random token instead), so even a remote bind is safe."""
    import logging

    with caplog.at_level(logging.INFO, logger="cypher.backend"):
        _check_tokens(_FakeSettings("change-me-user-token", "change-me-agent-token", api_host="100.64.1.2"))
    assert any("placeholder" in r.message for r in caplog.records)


def test_weak_token_on_localhost_only_warns(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _check_tokens(_FakeSettings("short-token", STRONG))
    assert any("weak" in r.message for r in caplog.records)


@pytest.mark.parametrize("host", ["0.0.0.0", "100.64.1.2", "192.168.1.50"])
def test_weak_token_beyond_localhost_refuses_to_start(host):
    with pytest.raises(RuntimeError, match="Refusing to start"):
        _check_tokens(_FakeSettings("short-token", STRONG, api_host=host))


def test_strong_tokens_beyond_localhost_start_quietly(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _check_tokens(_FakeSettings(STRONG, STRONG, api_host="100.64.1.2"))
    assert not caplog.records


def test_placeholder_set_matches_env_example():
    assert _PLACEHOLDER_TOKENS == {"change-me-user-token", "change-me-agent-token"}
