def test_create_message_is_pending(client, user_headers):
    resp = client.post(
        "/api/messages", json={"content": "Erstelle eine Aufgabe."}, headers=user_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["response"] is None


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
