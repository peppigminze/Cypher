"""
Lifecycle of the local chat model in Ollama.

Responsibilities:
  * warm-up at startup with EXACTLY the options real requests use
    (num_ctx, keep_alive, think) - a warm-up with different options would
    make Ollama reload the runner on the first real request anyway;
  * single-model policy: unload other resident models so nothing spills
    into shared memory (docs/overhaul/AUDIT.md K1);
  * status for /api/health: reachable, installed, loaded, % on GPU,
    shared-memory spill;
  * recovery: when Ollama comes back after being unreachable, warm up
    again without a backend restart.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.config import Settings
from app.core.gpu import GpuMemorySnapshot, read_gpu_memory
from app.llm.base import LLMUnavailableError, ModelNotFoundError, collect
from app.llm.factory import build_local_provider
from app.llm.ollama import OllamaAdmin, model_matches

logger = logging.getLogger("cypher.llm.manager")

_GPU_CACHE_SECONDS = 10.0
_WARMUP_RETRY_SECONDS = 60.0
# A health check must not hang while Ollama is starting up and accepts
# connections but doesn't answer yet.
_PROBE_TIMEOUT_SECONDS = 2.0


class WarmupState(str, enum.Enum):
    DISABLED = "disabled"
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


@dataclass
class LLMStatus:
    reachable: bool
    model: str
    version: Optional[str] = None
    installed: Optional[bool] = None
    loaded: bool = False
    size_gb: Optional[float] = None
    vram_gb: Optional[float] = None
    gpu_share: Optional[float] = None
    context_length: Optional[int] = None
    other_loaded: List[str] = field(default_factory=list)
    warmup: str = WarmupState.PENDING.value
    warmup_seconds: Optional[float] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    gpu: Optional[Dict[str, Any]] = None
    checked_at: float = 0.0

    @property
    def spill_gb(self) -> Optional[float]:
        if not self.gpu:
            return None
        return self.gpu.get("inference_shared_gb")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["spill_gb"] = self.spill_gb
        return data


class ModelManager:
    def __init__(
        self,
        settings: Settings,
        admin: Optional[OllamaAdmin] = None,
        gpu_reader: Callable[[], Optional[GpuMemorySnapshot]] = read_gpu_memory,
        provider_factory: Callable[..., Any] = build_local_provider,
    ) -> None:
        self.settings = settings
        self.model = settings.LOCAL_LLM_MODEL
        self.admin = admin or OllamaAdmin(settings.LOCAL_LLM_BASE_URL, timeout_seconds=min(_PROBE_TIMEOUT_SECONDS, settings.LOCAL_LLM_TIMEOUT_SECONDS))
        self._gpu_reader = gpu_reader
        self._provider_factory = provider_factory
        self.warmup_state = WarmupState.PENDING if settings.LLM_WARMUP_ON_START else WarmupState.DISABLED
        self.warmup_seconds: Optional[float] = None
        self.warmup_error: Optional[str] = None
        self._status: Optional[LLMStatus] = None
        self._status_lock = asyncio.Lock()
        self._warmup_lock = asyncio.Lock()
        self._gpu_cache: tuple[float, Optional[GpuMemorySnapshot]] = (0.0, None)
        self._was_reachable: Optional[bool] = None
        self._last_warmup_attempt = 0.0

    # ---------------------------------------------------------------- status
    async def status(self, max_age: float = 2.0) -> LLMStatus:
        cached = self._status
        if cached and time.monotonic() - cached.checked_at < max_age:
            return cached
        async with self._status_lock:
            cached = self._status
            if cached and time.monotonic() - cached.checked_at < max_age:
                return cached
            self._status = await self._probe()
            return self._status

    async def _probe(self) -> LLMStatus:
        st = LLMStatus(reachable=False, model=self.model, warmup=self.warmup_state.value, warmup_seconds=self.warmup_seconds, checked_at=time.monotonic())
        try:
            st.version = await self.admin.version()
            st.reachable = True
            installed = await self.admin.installed_models()
            st.installed = any(model_matches(name, self.model) for name in installed)
            for entry in await self.admin.loaded_models():
                name = entry.get("name") or entry.get("model") or ""
                if model_matches(name, self.model):
                    st.loaded = True
                    size = entry.get("size") or 0
                    vram = entry.get("size_vram") or 0
                    st.size_gb = round(size / 1e9, 2) if size else None
                    st.vram_gb = round(vram / 1e9, 2) if vram else None
                    st.gpu_share = round(vram / size, 3) if size else None
                    st.context_length = entry.get("context_length")
                else:
                    st.other_loaded.append(name)
        except LLMUnavailableError as exc:
            st.error = str(exc)
            st.error_code = "ollama_unreachable"
        if st.reachable and st.installed is False:
            st.error_code = "model_missing"
            st.error = f"Model {self.model} is not installed."
        if self.warmup_state == WarmupState.FAILED and self.warmup_error and not st.error:
            st.error = self.warmup_error
        snapshot = await self._gpu_snapshot()
        st.gpu = asdict(snapshot) if snapshot else None
        if snapshot:
            st.gpu["vram_total_gb"] = snapshot.vram_total_gb
            st.gpu["adapter"] = snapshot.primary_adapter_name
        return st

    async def _gpu_snapshot(self) -> Optional[GpuMemorySnapshot]:
        at, cached = self._gpu_cache
        if time.monotonic() - at < _GPU_CACHE_SECONDS:
            return cached
        try:
            snapshot = await asyncio.wait_for(asyncio.to_thread(self._gpu_reader), timeout=3.0)
        except (asyncio.TimeoutError, OSError) as exc:
            logger.debug("GPU snapshot failed: %s", exc)
            snapshot = None
        self._gpu_cache = (time.monotonic(), snapshot)
        return snapshot

    def invalidate(self) -> None:
        self._status = None
        self._gpu_cache = (0.0, None)

    # ---------------------------------------------------------------- warm-up
    async def warm_up(self) -> bool:
        """Load the model into VRAM. Safe to call repeatedly/concurrently."""
        async with self._warmup_lock:
            self._last_warmup_attempt = time.monotonic()
            self.warmup_state = WarmupState.RUNNING
            self.warmup_error = None
            started = time.perf_counter()
            try:
                installed = await self.admin.installed_models()
                if not any(model_matches(n, self.model) for n in installed):
                    raise ModelNotFoundError(self.model, base_url=self.settings.LOCAL_LLM_BASE_URL)
                if self.settings.LLM_SINGLE_MODEL_POLICY:
                    await self.unload_others()
                provider = self._provider_factory(self.settings)
                # One generated token with the production options loads the
                # runner exactly as later requests need it.
                await collect(provider.stream_chat([{"role": "user", "content": "Hallo"}], max_tokens=1))
            except LLMUnavailableError as exc:
                self.warmup_state = WarmupState.FAILED
                self.warmup_error = str(exc)
                logger.warning("LLM warm-up failed: %s", exc)
                self.invalidate()
                return False
            self.warmup_seconds = round(time.perf_counter() - started, 2)
            self.warmup_state = WarmupState.READY
            logger.info("LLM %s warm in %.1fs (num_ctx=%s, keep_alive=%s)", self.model, self.warmup_seconds, self.settings.LOCAL_LLM_NUM_CTX, self.settings.LOCAL_LLM_KEEP_ALIVE)
            self.invalidate()
            return True

    async def unload_others(self) -> List[str]:
        unloaded = []
        for entry in await self.admin.loaded_models():
            name = entry.get("name") or entry.get("model") or ""
            if name and not model_matches(name, self.model):
                logger.info("Single-model policy: unloading %s to keep VRAM free", name)
                await self.admin.unload(name)
                unloaded.append(name)
        return unloaded

    # ---------------------------------------------------------------- monitor
    async def monitor(self, interval: float) -> None:
        """Background loop: refresh status; after Ollama was unreachable and
        is back (e.g. restarted by the user), warm the model up again."""
        while True:
            try:
                st = await self.status(max_age=0)
                came_back = self._was_reachable is False and st.reachable
                # Back off on repeated failures (e.g. model too large) instead of hammering Ollama.
                retry_failed = (
                    st.reachable
                    and st.installed
                    and self.warmup_state == WarmupState.FAILED
                    and time.monotonic() - self._last_warmup_attempt > _WARMUP_RETRY_SECONDS
                )
                if self.warmup_state != WarmupState.DISABLED and (came_back or retry_failed):
                    logger.info("Ollama reachable again - warming up %s", self.model)
                    await self.warm_up()
                if self._was_reachable and not st.reachable:
                    logger.warning("Ollama became unreachable (%s)", st.error)
                self._was_reachable = st.reachable
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the monitor must never die
                logger.exception("LLM monitor iteration failed")
            await asyncio.sleep(interval)
