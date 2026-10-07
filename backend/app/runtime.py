"""
Process-wide runtime: owns long-lived components and background tasks.

Startup is non-blocking: the HTTP server accepts requests immediately
while the LLM, Whisper and the TTS voice warm up in the background.
/api/health reports each component's warm-up state, so the UI can show
"lädt noch" instead of the first request silently taking 15 seconds.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from app.config import Settings, get_settings
from app.llm.http import aclose_all
from app.llm.model_manager import ModelManager, WarmupState

logger = logging.getLogger("cypher.runtime")


@dataclass
class ComponentWarmup:
    name: str
    state: WarmupState
    seconds: Optional[float] = None
    error: Optional[str] = None


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_manager = ModelManager(settings)
        self.stt = ComponentWarmup("stt", WarmupState.PENDING if settings.WHISPER_WARMUP_ON_START else WarmupState.DISABLED)
        self.tts = ComponentWarmup("tts", WarmupState.PENDING if settings.TTS_WARMUP_ON_START else WarmupState.DISABLED)
        self.started_at = time.time()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        s = self.settings
        if s.LLM_WARMUP_ON_START:
            self._spawn(self.model_manager.warm_up(), "llm-warmup")
        if s.LLM_MONITOR_INTERVAL_SECONDS > 0:
            self._spawn(self.model_manager.monitor(s.LLM_MONITOR_INTERVAL_SECONDS), "llm-monitor")
        if s.WHISPER_WARMUP_ON_START:
            from app.stt import whisper_provider  # noqa: PLC0415 - heavy import, only when used

            self._spawn(self._warm(self.stt, whisper_provider.warm_up), "stt-warmup")
        if s.TTS_WARMUP_ON_START:
            from app.tts import piper_provider  # noqa: PLC0415

            self._spawn(self._warm(self.tts, piper_provider.warm_up), "tts-warmup")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutting down
                pass
        self._tasks.clear()
        await aclose_all()

    def _spawn(self, coro, name: str) -> None:
        self._tasks.append(asyncio.create_task(coro, name=name))

    async def _warm(self, component: ComponentWarmup, fn: Callable[[Settings], float]) -> None:
        component.state = WarmupState.RUNNING
        try:
            # CPU-bound model loading must not block the event loop.
            component.seconds = round(await asyncio.to_thread(fn, self.settings), 2)
            component.state = WarmupState.READY
            logger.info("%s warm in %.1fs", component.name, component.seconds)
        except Exception as exc:  # noqa: BLE001 - reported via /api/health
            component.state = WarmupState.FAILED
            component.error = str(exc)
            logger.warning("%s warm-up failed: %s", component.name, exc)


_runtime: Optional[Runtime] = None


def get_runtime() -> Runtime:
    """The current runtime. Created lazily so tests and scripts that never
    ran the FastAPI lifespan still get a working instance."""
    global _runtime
    if _runtime is None:
        _runtime = Runtime(get_settings())
    return _runtime


def set_runtime(runtime: Optional[Runtime]) -> None:
    global _runtime
    _runtime = runtime
