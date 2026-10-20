"""/api/chat: text chat on the conversation engine, streamed as SSE (M5)."""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from app.api import chat as chat_api
from app.tools import pending_approvals
from app.tools.registry import ToolRegistry

from tests.test_engine import NoteTool, ScriptedLLM, call


def events(resp) -> List[Dict[str, Any]]:
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


@pytest.fixture(autouse=True)
def _clean():
    pending_approvals.clear()
    yield
    pending_approvals.clear()


@pytest.fixture()
def scripted(monkeypatch):
    holder: Dict[str, Any] = {}

    def use(steps):
        llm = ScriptedLLM(steps)
        holder["llm"] = llm
        monkeypatch.setattr(chat_api, "get_provider_by_name", lambda name, settings=None: llm)
        registry = ToolRegistry()
        registry.register(NoteTool())
        holder["registry"] = registry
        monkeypatch.setattr(chat_api, "build_registry", lambda: registry)
        return llm

    return use, holder


def test_chat_streams_deltas_and_reply(client, user_headers, scripted):
    use, _ = scripted
    use([["Hallo ", "Silvan."]])
    resp = client.post("/api/chat", json={"message": "Hallo"}, headers=user_headers)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    evs = events(resp)
    assert evs[0]["type"] == "conversation" and len(evs[0]["conversation_id"]) == 16
    assert [e["text"] for e in evs if e["type"] == "reply_delta"] == ["Hallo ", "Silvan."]
    assert evs[-1]["type"] == "reply" and evs[-1]["text"] == "Hallo Silvan."


def test_chat_continues_a_conversation_and_shows_history(client, user_headers, scripted):
    use, holder = scripted
    use([["Erste Antwort."], ["Zweite Antwort."]])
    cid = events(client.post("/api/chat", json={"message": "Eins"}, headers=user_headers))[0]["conversation_id"]
    client.post("/api/chat", json={"message": "Zwei", "conversation_id": cid}, headers=user_headers)
    assert [m["role"] for m in holder["llm"].calls[1][1:]] == ["user", "assistant", "user"]
    turns = client.get("/api/chat/history", params={"conversation_id": cid}, headers=user_headers).json()["turns"]
    assert turns == [
        {"role": "user", "text": "Eins"}, {"role": "assistant", "text": "Erste Antwort."},
        {"role": "user", "text": "Zwei"}, {"role": "assistant", "text": "Zweite Antwort."},
    ]


def test_chat_approval_card_and_resume(client, user_headers, scripted):
    use, holder = scripted
    use([[call("save_note", {"text": "Brot"})], ["Notiz gespeichert."]])
    evs = events(client.post("/api/chat", json={"message": "Notiere Brot"}, headers=user_headers))
    card = next(e for e in evs if e["type"] == "approval")["card"]
    assert card["risk"] == "write" and "Brot" in card["summary"]
    assert holder["registry"].get("save_note").runs == []
    resumed = events(client.post("/api/chat/approval", json={"approval_id": card["approval_id"], "approved": True}, headers=user_headers))
    assert holder["registry"].get("save_note").runs == [{"text": "Brot"}]
    assert resumed[-1] == {"type": "reply", "text": "Notiz gespeichert.", "metrics": resumed[-1]["metrics"]}
    # Single use: a second "yes" can't run it again.
    again = client.post("/api/chat/approval", json={"approval_id": card["approval_id"], "approved": True}, headers=user_headers)
    assert again.status_code == 404


def test_chat_reports_llm_down_in_german(client, user_headers):
    evs = events(client.post("/api/chat", json={"message": "Hallo"}, headers=user_headers))
    assert evs[-1]["type"] == "error" and evs[-1]["code"] == "ollama_unreachable"
    assert "Sprachmodell" in evs[-1]["message"]


def test_chat_requires_auth_and_valid_ids(client, user_headers):
    assert client.post("/api/chat", json={"message": "x"}).status_code == 401
    assert client.get("/api/chat/history", params={"conversation_id": "../x"}, headers=user_headers).status_code == 422


def test_history_hides_engine_context_lines():
    assert chat_api.display_text("[Kontext: Jetzt ist Montag, 12:00 Uhr.]\n[Aus deinem Gedächtnis (Notizen): „x“]\nWie geht's?") == "Wie geht's?"


# ------------------------------------------------------------------ cloud only after asking (M5)
def test_local_failure_offers_the_cloud_and_runs_it_only_after_yes(client, user_headers, monkeypatch):
    from app.agent import cloud_offer
    from app.llm.base import LLMUnavailableError

    class DownLLM(ScriptedLLM):
        async def stream_chat(self, messages, tools=None, **kwargs):
            raise LLMUnavailableError("down", provider="local", base_url="http://127.0.0.1:11434")
            yield  # pragma: no cover

    cloud = ScriptedLLM([["Antwort aus der Cloud."]])
    used = []

    def provider(name, settings=None):
        used.append(name)
        return cloud if name == "cloud" else DownLLM([])

    monkeypatch.setattr(chat_api, "get_provider_by_name", provider)
    monkeypatch.setattr(cloud_offer, "cloud_available", lambda settings=None: True)
    evs = events(client.post("/api/chat", json={"message": "Erklär mir Photosynthese"}, headers=user_headers))
    assert [e["type"] for e in evs] == ["conversation", "error", "approval"]
    card = evs[-1]["card"]
    assert card["risk"] == "cloud" and card["approval_id"].startswith("cloud-")
    assert used == ["local"]  # nothing went to the cloud yet

    answer = events(client.post("/api/chat/approval", json={"approval_id": card["approval_id"], "approved": True}, headers=user_headers))
    assert answer[-1]["text"] == "Antwort aus der Cloud." and used == ["local", "cloud"]
    # The retried question is in the history once, with the cloud's answer.
    sent = cloud.calls[0]
    assert [m["role"] for m in sent[1:]] == ["user"] and sent[-1]["content"].endswith("Erklär mir Photosynthese")


def test_no_cloud_offer_when_cloud_is_not_configured(client, user_headers):
    evs = events(client.post("/api/chat", json={"message": "Hallo"}, headers=user_headers))
    assert [e["type"] for e in evs] == ["conversation", "error"]


def test_declined_cloud_offer(client, user_headers):
    from app.agent import cloud_offer

    offer = cloud_offer.create("abcdabcdabcdabcd", "Hallo", "text")
    evs = events(client.post("/api/chat/approval", json={"approval_id": offer.offer_id, "approved": False}, headers=user_headers))
    assert evs[-1]["text"] == "Okay, dann nicht."
    assert client.post("/api/chat/approval", json={"approval_id": offer.offer_id, "approved": True}, headers=user_headers).status_code == 404
