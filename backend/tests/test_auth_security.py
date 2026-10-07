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


def test_warns_on_placeholder_user_token(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _check_tokens(_FakeSettings("change-me-user-token", "real-token-xyz"))
    assert any("placeholder" in r.message for r in caplog.records)


def test_warns_on_placeholder_agent_token(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _check_tokens(_FakeSettings("real-token-xyz", "change-me-agent-token"))
    assert any("placeholder" in r.message for r in caplog.records)


def test_no_warning_for_real_tokens(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _check_tokens(_FakeSettings("real-user-token-xyz", "real-agent-token-abc"))
    assert not any("placeholder" in r.message for r in caplog.records)


# ------------------------------------------------------- Phase 8: Tailscale/remote bind hardening


def test_placeholder_tokens_localhost_only_still_just_warns(caplog):
    """Unchanged from before Phase 8: API_HOST=127.0.0.1 (the default)
    with placeholder tokens is low-stakes - still just a warning, not a
    hard failure, so existing local-only setups aren't broken by this."""
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _check_tokens(_FakeSettings("change-me-user-token", "change-me-agent-token", api_host="127.0.0.1"))
    assert any("placeholder" in r.message for r in caplog.records)


@pytest.mark.parametrize("host", ["0.0.0.0", "100.64.1.2", "192.168.1.50"])
def test_placeholder_tokens_beyond_localhost_refuses_to_start(host):
    """The actual Phase 8 hardening: a placeholder token combined with a
    bind address reachable beyond this machine (Tailscale IP, LAN, or a
    wide-open 0.0.0.0) is refused outright instead of just logged,
    since anyone who has ever seen this public repo's default token
    value could otherwise authenticate."""
    with pytest.raises(RuntimeError, match="Refusing to start"):
        _check_tokens(_FakeSettings("change-me-user-token", "real-token-xyz", api_host=host))


def test_real_tokens_beyond_localhost_do_not_raise(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="cypher.backend"):
        _check_tokens(_FakeSettings("real-user-token-xyz", "real-agent-token-abc", api_host="100.64.1.2"))
    assert not any("placeholder" in r.message for r in caplog.records)


def test_placeholder_set_matches_env_example():
    assert _PLACEHOLDER_TOKENS == {"change-me-user-token", "change-me-agent-token"}
