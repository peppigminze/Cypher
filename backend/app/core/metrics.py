"""
In-process latency metrics per pipeline stage.

Kept deliberately simple (no Prometheus dependency): a bounded ring
buffer per stage, from which the diagnostics endpoint computes p50/p90
and the last value. Stages used across the app:

    stt            speech-to-text for one utterance
    llm_ttft       time until the LLM's first token
    llm_total      one full LLM call
    tool           one tool execution
    tts_first      synthesis of the first sentence of a reply
    tts            one synthesis call
    e2e_first_audio  end of user speech -> first audio chunk sent
    http           one HTTP request (middleware)
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Deque, Dict, Iterator

logger = logging.getLogger("cypher.metrics")

_WINDOW = 200


class LatencyMetrics:
    def __init__(self, window: int = _WINDOW) -> None:
        self._window = window
        self._data: Dict[str, Deque[float]] = {}
        # Recorded from worker threads (STT/TTS run via asyncio.to_thread) too.
        self._lock = threading.Lock()

    def record(self, stage: str, seconds: float) -> None:
        with self._lock:
            self._data.setdefault(stage, deque(maxlen=self._window)).append(seconds)
        logger.debug("latency %s=%.3fs", stage, seconds, extra={"stage": stage, "duration_ms": round(seconds * 1000, 1)})

    @contextmanager
    def timed(self, stage: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(stage, time.perf_counter() - started)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            data = {k: list(v) for k, v in self._data.items()}
        out: Dict[str, Dict[str, float]] = {}
        for stage, values in data.items():
            if not values:
                continue
            ordered = sorted(values)
            out[stage] = {
                "count": len(values),
                "last_ms": round(values[-1] * 1000, 1),
                "p50_ms": round(statistics.median(ordered) * 1000, 1),
                "p90_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))] * 1000, 1),
            }
        return out

    def reset(self) -> None:
        with self._lock:
            self._data.clear()


metrics = LatencyMetrics()
