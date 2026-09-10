"""
Regression tests for the security-hardening pass on app/auth.py and
app/main.py (project spec section 37).
"""
from __future__ import annotations

from app.auth import _tokens_match
from app.main import _PLACEHOLDER_TOKENS, _warn_if_default_tokens


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
    def __init__(self, user_token: str, agent_token: str):
        self.USER_TOKEN = user_token
        self.AGENT_TOKEN = agent_token


def test_warns_on_placeholder_user_token(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _warn_if_default_tokens(_FakeSettings("change-me-user-token", "real-token-xyz"))
    assert any("placeholder" in r.message for r in caplog.records)


def test_warns_on_placeholder_agent_token(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _warn_if_default_tokens(_FakeSettings("real-token-xyz", "change-me-agent-token"))
    assert any("placeholder" in r.message for r in caplog.records)


def test_no_warning_for_real_tokens(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _warn_if_default_tokens(_FakeSettings("real-user-token-xyz", "real-agent-token-abc"))
    assert not any("placeholder" in r.message for r in caplog.records)


def test_placeholder_set_matches_env_example():
    assert _PLACEHOLDER_TOKENS == {"change-me-user-token", "change-me-agent-token"}
