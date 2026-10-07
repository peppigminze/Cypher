"""ModelManager against a stateful fake Ollama server."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.core.gpu import GpuAdapterInfo, GpuMemorySnapshot
from app.llm.model_manager import ModelManager, WarmupState
from app.llm.ollama import OllamaAdmin, OllamaProvider


class FakeOllama:
    def __init__(self, installed=("qwen3.5:9b", "qwen3.5:4b"), loaded=()):
        self.up = True
        self.installed = list(installed)
        self.loaded = {name: {"name": name, "size": 6_000_000_000, "size_vram": 6_000_000_000, "context_length": 8192} for name in loaded}
        self.unloaded: list[str] = []
        self.chat_payloads: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.0"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.installed]})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": list(self.loaded.values())})
        if path == "/api/generate":
            body = json.loads(request.content)
            if body.get("keep_alive") == 0:
                self.loaded.pop(body["model"], None)
                self.unloaded.append(body["model"])
            return httpx.Response(200, json={"done": True})
        if path == "/api/chat":
            body = json.loads(request.content)
            self.chat_payloads.append(body)
            if body["model"] not in self.installed:
                return httpx.Response(404, json={"error": "model not found"})
            self.loaded[body["model"]] = {"name": body["model"], "size": 6_000_000_000, "size_vram": 6_000_000_000, "context_length": body["options"]["num_ctx"]}
            return httpx.Response(200, content=(json.dumps({"message": {"content": "H"}, "done": True}) + "\n").encode())
        return httpx.Response(404)


def make_manager(fake: FakeOllama, gpu=None, **overrides) -> ModelManager:
    settings = Settings(_env_file=None, LOCAL_LLM_BASE_URL="http://ollama.test", **overrides)
    transport = httpx.MockTransport(fake.handler)

    def provider_factory(s, model=None):
        return OllamaProvider(s.LOCAL_LLM_BASE_URL, model or s.LOCAL_LLM_MODEL, num_ctx=s.LOCAL_LLM_NUM_CTX, keep_alive=s.LOCAL_LLM_KEEP_ALIVE, transport=transport)

    return ModelManager(settings, admin=OllamaAdmin("http://ollama.test", transport=transport), gpu_reader=lambda: gpu, provider_factory=provider_factory)


async def test_warm_up_loads_model_with_production_options_and_unloads_others():
    fake = FakeOllama(loaded=("llama3.1:8b", "qwen3.5:4b"))
    manager = make_manager(fake, LOCAL_LLM_NUM_CTX=8192, LOCAL_LLM_KEEP_ALIVE="30m")

    assert await manager.warm_up() is True
    assert manager.warmup_state == WarmupState.READY
    assert sorted(fake.unloaded) == ["llama3.1:8b", "qwen3.5:4b"]
    payload = fake.chat_payloads[-1]
    assert payload["options"]["num_ctx"] == 8192 and payload["keep_alive"] == "30m" and payload["think"] is False

    st = await manager.status(max_age=0)
    assert st.loaded and st.gpu_share == 1.0 and st.other_loaded == []


async def test_single_model_policy_can_be_disabled():
    fake = FakeOllama(loaded=("llama3.1:8b",))
    manager = make_manager(fake, LLM_SINGLE_MODEL_POLICY=False)
    await manager.warm_up()
    assert fake.unloaded == []


async def test_missing_model_fails_warm_up_with_clear_status():
    fake = FakeOllama(installed=("qwen3.5:4b",))
    manager = make_manager(fake)
    assert await manager.warm_up() is False
    assert manager.warmup_state == WarmupState.FAILED
    st = await manager.status(max_age=0)
    assert st.installed is False and st.error_code == "model_missing"


async def test_unreachable_ollama_reports_error_without_raising():
    fake = FakeOllama()
    fake.up = False
    manager = make_manager(fake)
    st = await manager.status(max_age=0)
    assert st.reachable is False and st.error_code == "ollama_unreachable"
    assert await manager.warm_up() is False


async def test_monitor_rewarms_after_ollama_comes_back():
    fake = FakeOllama()
    manager = make_manager(fake)
    await manager.warm_up()
    fake.up = False
    fake.loaded.clear()  # an Ollama restart empties VRAM

    task = asyncio.create_task(manager.monitor(interval=0.01))
    await asyncio.sleep(0.05)
    assert manager._was_reachable is False
    fake.up = True
    for _ in range(100):
        await asyncio.sleep(0.01)
        if "qwen3.5:9b" in fake.loaded:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "qwen3.5:9b" in fake.loaded
    assert manager.warmup_state == WarmupState.READY


async def test_status_includes_gpu_spill_from_counters():
    snapshot = GpuMemorySnapshot(
        adapters=[GpuAdapterInfo("AMD Radeon RX 9070 XT", 15.92)],
        dedicated_used_gb=15.8, shared_used_gb=8.9, inference_dedicated_gb=9.0, inference_shared_gb=3.2, inference_pids=[1],
    )
    manager = make_manager(FakeOllama(loaded=("qwen3.5:9b",)), gpu=snapshot)
    st = await manager.status(max_age=0)
    assert st.spill_gb == 3.2
    assert st.gpu["adapter"] == "AMD Radeon RX 9070 XT"
    assert st.gpu["vram_total_gb"] == 15.92


async def test_status_is_cached_between_calls():
    fake = FakeOllama()
    manager = make_manager(fake)
    first = await manager.status(max_age=60)
    fake.up = False
    assert (await manager.status(max_age=60)) is first
