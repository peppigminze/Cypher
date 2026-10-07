from __future__ import annotations

from sqlalchemy.orm import sessionmaker


def test_usage_requires_auth(client):
    resp = client.get("/api/usage")
    assert resp.status_code == 401


def test_usage_is_empty_by_default(client, user_headers):
    resp = client.get("/api/usage", headers=user_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["today"]["total"]["total_tokens"] == 0
    assert body["today"]["by_provider"] == {}
    assert body["month"]["total"]["total_tokens"] == 0


def test_usage_reports_recorded_tokens(client, user_headers, test_engine):
    from app.settings_store import record_token_usage

    Session = sessionmaker(bind=test_engine)
    session = Session()
    try:
        record_token_usage(session, "cloud", "gpt-5.6-luna", {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
        record_token_usage(session, "local", "qwen2.5:7b", {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
    finally:
        session.close()

    resp = client.get("/api/usage", headers=user_headers)
    body = resp.json()

    assert body["today"]["total"]["total_tokens"] == 40
    assert body["today"]["by_provider"]["cloud"]["total_tokens"] == 30
    assert body["today"]["by_provider"]["local"]["total_tokens"] == 10
    assert body["month"]["total"]["total_tokens"] == 40
