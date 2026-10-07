"""Metrics, error catalog mapping, logging setup and the local-time tool."""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pytest

from app.config import Settings, get_settings
from app.core import errors
from app.core.log import read_log_tail, setup_logging
from app.core.metrics import LatencyMetrics
from app.llm.base import LLMResponseError, LLMTimeoutError, LLMUnavailableError, ModelNotFoundError
from app.tools.system_tools import GetCurrentTimeTool, german_date


def test_metrics_percentiles():
    m = LatencyMetrics()
    for v in (0.1, 0.2, 0.3, 0.4, 1.0):
        m.record("stt", v)
    snap = m.snapshot()["stt"]
    assert snap["count"] == 5
    assert snap["p50_ms"] == 300.0
    assert snap["last_ms"] == 1000.0


def test_metrics_timed_context_manager():
    m = LatencyMetrics()
    with m.timed("tool"):
        pass
    assert m.snapshot()["tool"]["count"] == 1


@pytest.mark.parametrize(
    ("exc", "code", "status"),
    [
        (ModelNotFoundError("qwen3.5:9b"), "model_missing", 503),
        (LLMTimeoutError(60), "llm_timeout", 504),
        (LLMResponseError("bad"), "llm_bad_response", 502),
        (LLMUnavailableError("down", base_url="http://x"), "ollama_unreachable", 503),
        (LLMUnavailableError("down", provider="cloud"), "cloud_unavailable", 503),
        (RuntimeError("boom"), "internal_error", 500),
    ],
)
def test_exceptions_map_to_catalog_entries(exc, code, status):
    err = errors.from_exception(exc)
    assert err.code == code
    assert err.http_status == status
    assert err.message and err.hint
    # Spoken text never contains technical details.
    assert "boom" not in err.spoken and "http" not in err.spoken


def test_json_log_file_carries_request_id(tmp_path):
    settings = Settings(_env_file=None, LOG_DIR=str(tmp_path), LOG_JSON=True, LOG_LEVEL="INFO")
    try:
        setup_logging(settings)
        from app.core.context import request_id_var

        token = request_id_var.set("req-test-1")
        logging.getLogger("cypher.test").info("hello %s", "world", extra={"stage": "stt"})
        request_id_var.reset(token)
        for h in logging.getLogger().handlers:
            h.flush()
        line = (tmp_path / "cypher.log").read_text(encoding="utf-8").strip().splitlines()[-1]
        entry = json.loads(line)
        assert entry["msg"] == "hello world"
        assert entry["request_id"] == "req-test-1"
        assert entry["stage"] == "stt"
        assert read_log_tail(settings, max_lines=5)[-1]["msg"] == "hello world"
    finally:
        setup_logging(get_settings())


def test_setup_logging_is_idempotent(tmp_path):
    settings = Settings(_env_file=None, LOG_DIR=str(tmp_path))
    try:
        setup_logging(settings)
        count = len(logging.getLogger().handlers)
        setup_logging(settings)
        assert len(logging.getLogger().handlers) == count
    finally:
        setup_logging(get_settings())


async def test_time_tool_returns_local_time_in_configured_zone(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "Europe/Zurich")
    get_settings.cache_clear()
    try:
        result = await GetCurrentTimeTool().execute()
    finally:
        get_settings.cache_clear()
    assert result.success
    data = result.data
    assert data["timezone"] in ("CET", "CEST")
    assert len(data["local_time"]) == 5 and data["local_time"][2] == ":"
    assert "utc_iso" in data  # kept for older callers


def test_german_date_formatting():
    assert german_date(datetime(2026, 10, 7)) == "Mittwoch, 7. Oktober 2026"
    assert german_date(datetime(2026, 3, 1)) == "Sonntag, 1. März 2026"
