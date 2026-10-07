"""
Structured logging: human-readable console + rotating log file.

The file handler writes one JSON object per line (LOG_JSON=true) so the
diagnostics page and future tooling can parse it without regexes; the
console stays readable for a human watching the terminal. Both carry the
request/pipeline ID from app/core/context.py.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.core.context import pipeline_id_var, request_id_var

LOG_FILE_NAME = "cypher.log"
_CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s|%(pipeline_id)s] %(name)s: %(message)s"

# Attributes every LogRecord has - anything else was passed via `extra=`
# and is worth keeping in the JSON output (e.g. stage, duration_ms).
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "request_id", "pipeline_id"}

_configured_for: tuple | None = None


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.pipeline_id = pipeline_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "pipeline_id": getattr(record, "pipeline_id", "-"),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(settings: Settings) -> Path:
    """Configure the root logger. Idempotent: calling it again with the
    same settings (e.g. once per TestClient lifespan) is a no-op, so
    handlers are never duplicated."""
    global _configured_for
    log_dir = settings.log_dir_path
    key = (str(log_dir), settings.LOG_LEVEL, settings.LOG_JSON, settings.LOG_FILE_MAX_BYTES, settings.LOG_FILE_BACKUPS)
    log_file = log_dir / LOG_FILE_NAME
    if _configured_for == key:
        return log_file

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_cypher", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(settings.LOG_LEVEL)

    context_filter = ContextFilter()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt="%H:%M:%S"))
    console.addFilter(context_filter)
    console._cypher = True  # type: ignore[attr-defined]
    root.addHandler(console)

    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=settings.LOG_FILE_MAX_BYTES, backupCount=settings.LOG_FILE_BACKUPS, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(JsonFormatter() if settings.LOG_JSON else logging.Formatter(_CONSOLE_FORMAT))
    file_handler.addFilter(context_filter)
    file_handler._cypher = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    # Uvicorn's own handlers would print every line twice; let its loggers
    # propagate into ours instead. Access logging is done by our middleware
    # (with request IDs and durations), so uvicorn's is muted.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # httpx logs every request at INFO - far too noisy for streaming LLM calls.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # faster-whisper logs every processed clip at INFO; our STT metric covers it.
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)

    _configured_for = key
    return log_file


def read_log_tail(settings: Settings, max_lines: int = 200, min_level: str = "INFO") -> list[dict]:
    """Last `max_lines` JSON log entries at or above `min_level` - for the
    diagnostics/log view. Reads from the end of the file so a large log
    doesn't get loaded completely."""
    log_file = settings.log_dir_path / LOG_FILE_NAME
    if not log_file.exists():
        return []
    threshold = logging.getLevelName(min_level)
    with log_file.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 512_000))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    entries: list[dict] = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if logging.getLevelName(entry.get("level", "INFO")) >= threshold:
            entries.append(entry)
        if len(entries) >= max_lines:
            break
    entries.reverse()
    return entries
