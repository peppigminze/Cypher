"""
Health endpoints.

  GET /health           liveness only ("is the process up?") - unchanged, used by scripts
  GET /api/health       readiness per component, German messages - public on purpose:
                        the UI must be able to say "Ollama läuft nicht" before it has
                        a token, and it reveals no secrets or paths
  GET /api/diagnostics  full details, latencies, config summary - token required
  GET /api/logs         tail of the structured log file - token required
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth import require_user
from app.core.health import collect_health, diagnostics_report, public_report
from app.core.log import read_log_tail
from app.runtime import get_runtime

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/api/health")
async def api_health() -> dict:
    runtime = get_runtime()
    return public_report(await collect_health(runtime), runtime)


@router.get("/api/diagnostics")
async def api_diagnostics(_user=Depends(require_user)) -> dict:
    runtime = get_runtime()
    return diagnostics_report(await collect_health(runtime), runtime)


@router.get("/api/logs")
def api_logs(
    _user=Depends(require_user),
    limit: int = Query(200, ge=1, le=2000),
    level: str = Query("INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$"),
) -> dict:
    return {"entries": read_log_tail(get_runtime().settings, max_lines=limit, min_level=level)}
