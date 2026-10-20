"""Approval card data + audit trail through the HTTP API."""
from __future__ import annotations

import json

from tests.llm_doubles import ChatWithToolsResponse, OneShotLLM
from app.tools import pending_approvals


class ScriptedLLM(OneShotLLM):
    name = "local"
    model = "scripted"

    def __init__(self, turns):
        self.turns = list(turns)

    async def chat(self, messages, temperature=0.3):
        return ""

    async def chat_with_tools(self, messages, tools, temperature=0.2):
        return ChatWithToolsResponse(message=self.turns.pop(0), usage=None)

    async def health_check(self):
        return True


def note_call():
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "save_note", "arguments": json.dumps({"text": "Milch kaufen"})}}]}


def test_note_flow_requires_approval_and_is_audited(client, user_headers, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setenv("NOTES_DIR", str(tmp_path))
    get_settings.cache_clear()
    llm = ScriptedLLM([note_call(), {"role": "assistant", "content": "Notiz gespeichert."}])
    monkeypatch.setattr("app.api.tool_chat.get_provider_by_name", lambda name: llm)
    try:
        resp = client.post("/api/tool-chat", json={"message": "Schreib eine Notiz: Milch kaufen", "source": "voice"}, headers=user_headers)
        body = resp.json()
        card = body["pending_confirmation"]
        assert card["tool_name"] == "save_note" and card["risk"] == "write"
        assert "Milch kaufen" in card["summary"] and card["expires_in_seconds"] > 0
        assert list(tmp_path.glob("*.md")) == []  # nothing written yet

        listed = client.get("/api/approvals", headers=user_headers).json()["pending"]
        assert [p["approval_id"] for p in listed] == [card["approval_id"]]

        done = client.post("/api/tool-chat/confirm", json={"approval_id": card["approval_id"], "approved": True}, headers=user_headers).json()
        assert done["reply"] == "Notiz gespeichert."
        assert len(list(tmp_path.glob("*.md"))) == 1

        decisions = [e["decision"] for e in client.get("/api/audit?tool=save_note", headers=user_headers).json()["entries"]]
        assert decisions[:2] == ["approved", "approval_requested"]
    finally:
        get_settings.cache_clear()
        pending_approvals.clear()


def test_rejected_note_is_not_written_and_reply_is_honest(client, user_headers, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setenv("NOTES_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr("app.api.tool_chat.get_provider_by_name", lambda name: ScriptedLLM([note_call()]))
    try:
        card = client.post("/api/tool-chat", json={"message": "Notiz: Milch"}, headers=user_headers).json()["pending_confirmation"]
        done = client.post("/api/tool-chat/confirm", json={"approval_id": card["approval_id"], "approved": False}, headers=user_headers).json()
        assert done["reply"] == "Okay, ich habe es nicht ausgeführt."
        assert list(tmp_path.glob("*.md")) == []
        assert client.get("/api/audit?tool=save_note", headers=user_headers).json()["entries"][0]["decision"] == "rejected"
        # Single use.
        again = client.post("/api/tool-chat/confirm", json={"approval_id": card["approval_id"], "approved": True}, headers=user_headers)
        assert again.status_code == 404
    finally:
        get_settings.cache_clear()
        pending_approvals.clear()


def test_tools_endpoint_lists_risk_tiers(client, user_headers):
    tools = {t["name"]: t for t in client.get("/api/tools", headers=user_headers).json()["tools"]}
    assert tools["run_command"]["risk"] == "critical" and tools["run_command"]["exposed"] is True
    assert tools["set_volume"]["risk"] == "control"
