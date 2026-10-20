"""/api/health, /api/diagnostics, /api/logs and the error/request-id plumbing."""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.core.gpu import GpuAdapterInfo, GpuMemorySnapshot
from app.llm.model_manager import ModelManager, WarmupState
from app.llm.ollama import OllamaAdmin
from app.runtime import Runtime, set_runtime
from tests.test_model_manager import FakeOllama


@pytest.fixture()
def fake_runtime(client):
    """Replace the runtime the app started with one whose Ollama is a fake."""
    fake = FakeOllama(loaded=("qwen3.5:9b",))
    settings = Settings(_env_file=None, LOCAL_LLM_BASE_URL="http://ollama.test")
    runtime = Runtime(settings)
    runtime.model_manager = ModelManager(
        settings,
        admin=OllamaAdmin("http://ollama.test", transport=httpx.MockTransport(fake.handler)),
        gpu_reader=lambda: GpuMemorySnapshot([GpuAdapterInfo("AMD Radeon RX 9070 XT", 15.92)], 11.0, 0.5, 6.0, 0.1, [1]),
    )
    runtime.model_manager.warmup_state = WarmupState.READY
    set_runtime(runtime)
    yield runtime, fake
    set_runtime(None)


def test_health_is_public_and_reports_ollama_down_in_german(client):
    # Test env points LOCAL_LLM_BASE_URL at a closed port.
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    ollama = body["components"]["ollama"]
    assert ollama["status"] == "error"
    assert "erreiche" in ollama["message"]
    assert "Ollama" in ollama["hint"]
    assert body["components"]["database"]["status"] == "ok"
    # Public endpoint: no internals.
    assert "details" not in ollama


def test_health_ok_when_everything_is_up(client, fake_runtime):
    body = client.get("/api/health").json()
    assert body["components"]["ollama"]["status"] == "ok"
    assert body["components"]["llm_model"]["status"] == "ok"
    assert "100 % GPU" in body["components"]["llm_model"]["message"]
    assert body["components"]["gpu"]["status"] == "ok"
    assert body["status"] in ("ok", "degraded")  # stt/tts depend on local voice files


def test_health_warns_on_vram_spill(client, fake_runtime):
    runtime, _ = fake_runtime
    runtime.model_manager._gpu_reader = lambda: GpuMemorySnapshot([GpuAdapterInfo("RX", 16.0)], 15.9, 9.0, 9.0, 3.5, [1])
    body = client.get("/api/health").json()
    gpu = body["components"]["gpu"]
    assert gpu["status"] == "warn"
    assert "ausgelagert" in gpu["message"]
    assert body["status"] == "degraded"


def test_health_reports_missing_model_with_pull_hint(client, fake_runtime):
    _, fake = fake_runtime
    fake.installed = ["llama3.1:8b"]
    fake.loaded.clear()
    body = client.get("/api/health").json()
    model = body["components"]["llm_model"]
    assert model["status"] == "error"
    assert "ollama pull qwen3.5:9b" in model["hint"]


def test_diagnostics_requires_token(client):
    assert client.get("/api/diagnostics").status_code == 401


def test_diagnostics_contains_details_latency_and_config(client, user_headers, fake_runtime):
    body = client.get("/api/diagnostics", headers=user_headers).json()
    assert body["component_details"]["llm_model"]["model"] == "qwen3.5:9b"
    assert body["config"]["num_ctx"] == 12288
    assert "latency" in body
    assert "USER_TOKEN" not in json.dumps(body)


def test_logs_endpoint_requires_token_and_returns_entries(client, user_headers):
    assert client.get("/api/logs").status_code == 401
    client.get("/")  # produces at least one access log line
    entries = client.get("/api/logs?level=DEBUG", headers=user_headers).json()["entries"]
    assert isinstance(entries, list)


def test_request_id_is_generated_and_echoed(client):
    resp = client.get("/")
    assert len(resp.headers["x-request-id"]) >= 8
    resp = client.get("/", headers={"X-Request-ID": "voice-abc123"})
    assert resp.headers["x-request-id"] == "voice-abc123"
    # Unsafe values are replaced, never echoed into logs.
    resp = client.get("/", headers={"X-Request-ID": "bad id\nINJECTED"})
    assert "INJECTED" not in resp.headers["x-request-id"]


def test_tool_chat_with_ollama_down_returns_german_error_object(client, user_headers):
    resp = client.post("/api/tool-chat", json={"message": "Wie spät ist es?"}, headers=user_headers)
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "ollama_unreachable"
    assert body["detail"] == body["error"]["message"]
    assert body["error"]["retryable"] is True
    assert body["error"]["request_id"] == resp.headers["x-request-id"]


def test_transcribe_rejects_empty_audio_in_german(client, user_headers):
    resp = client.post("/transcribe", headers=user_headers, files={"audio": ("a.webm", b"x" * 10, "audio/webm")})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "audio_invalid"
    assert "Aufnahme" in resp.json()["detail"]
