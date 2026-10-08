from app.api import messages as messages_api
from tests.test_engine import ScriptedLLM


def test_create_message_is_answered_directly(client, user_headers, monkeypatch):
    """M5: no separate agent process any more - the backend answers right away."""
    monkeypatch.setattr(messages_api, "get_provider_by_name", lambda name: ScriptedLLM([["Erledigt, ich habe nichts gemacht."]]))
    resp = client.post("/api/messages", json={"content": "Erzähl einen Witz."}, headers=user_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed" and body["response"] == "Erledigt, ich habe nichts gemacht."
    assert body["processed_by"] == "local" and body["processed_at"]


def test_create_message_reports_german_error_when_llm_is_down(client, user_headers):
    body = client.post("/api/messages", json={"content": "Erzähl einen Witz."}, headers=user_headers).json()
    assert body["status"] == "failed" and "Sprachmodell" in body["error"]


def test_create_message_idempotent_client_id(client, user_headers):
    payload = {"content": "Hallo Cypher", "client_id": "abc-123"}
    first = client.post("/api/messages", json=payload, headers=user_headers).json()
    second = client.post("/api/messages", json=payload, headers=user_headers).json()
    assert first["id"] == second["id"]

    resp = client.get("/api/messages", headers=user_headers)
    assert len(resp.json()) == 1


def test_list_messages_requires_auth(client):
    resp = client.get("/api/messages")
    assert resp.status_code == 401
