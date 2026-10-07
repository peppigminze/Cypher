"""
Component health for /api/health (public, compact) and /api/diagnostics
(authenticated, detailed).

Every component reports one of:
    ok        working
    warn      working with a caveat (still loading, partly on CPU, spilled VRAM)
    error     not working; `hint` says what to do
    unknown   cannot be determined on this system (e.g. GPU counters off-Windows)

Overall status: "error" if a core component (database, ollama, llm_model)
fails, "degraded" if anything else is not ok, otherwise "ok".
"""
from __future__ import annotations

import asyncio
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from sqlalchemy import text

from app.config import Settings
from app.core import errors
from app.llm.model_manager import LLMStatus, WarmupState
from app.runtime import ComponentWarmup, Runtime

CORE_COMPONENTS = ("database", "ollama", "llm_model")
APP_VERSION = "1.0.0-overhaul"


@dataclass
class ComponentHealth:
    status: str
    message: str
    hint: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def public(self) -> Dict[str, Any]:
        return {"status": self.status, "message": self.message, "hint": self.hint}


def _de(value: float) -> str:
    """German decimal comma for numbers shown/spoken to the user."""
    return f"{value:.1f}".replace(".", ",")


def _from_error(err: errors.CypherError, **details: Any) -> ComponentHealth:
    return ComponentHealth("error", err.message, err.hint, details)


async def check_database() -> ComponentHealth:
    from app.database.db import engine  # noqa: PLC0415

    def _ping() -> None:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(asyncio.to_thread(_ping), timeout=3.0)
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth("error", "Die Datenbank ist nicht erreichbar.", "Prüfe DATABASE_URL in der .env und ob die Datei gesperrt ist.", {"error": str(exc)})
    return ComponentHealth("ok", "Datenbank erreichbar.", details={"url": engine.url.render_as_string(hide_password=True)})


def check_ollama(st: LLMStatus, settings: Settings) -> ComponentHealth:
    if not st.reachable:
        return _from_error(errors.ollama_unreachable(settings.LOCAL_LLM_BASE_URL), error=st.error)
    return ComponentHealth("ok", f"Ollama {st.version} erreichbar.", details={"version": st.version, "base_url": settings.LOCAL_LLM_BASE_URL})


def check_llm_model(st: LLMStatus, settings: Settings) -> ComponentHealth:
    details = st.to_dict()
    if not st.reachable:
        return ComponentHealth("error", "Modellstatus unbekannt – Ollama ist nicht erreichbar.", "Erst Ollama starten.", details)
    if st.installed is False:
        return _from_error(errors.model_missing(st.model), **details)
    if st.warmup in (WarmupState.RUNNING.value, WarmupState.PENDING.value) and not st.loaded:
        return ComponentHealth("warn", f"{st.model} wird gerade geladen …", "Dauert beim ersten Start einige Sekunden.", details)
    if st.warmup == WarmupState.FAILED.value and not st.loaded:
        return ComponentHealth("error", f"{st.model} konnte nicht geladen werden.", "Details auf der Diagnose-Seite. Cypher versucht es automatisch erneut.", details)
    if not st.loaded:
        return ComponentHealth("warn", f"{st.model} ist gerade nicht im Grafikspeicher.", "Die nächste Antwort dauert ein paar Sekunden länger (Modell wird geladen).", details)
    gpu_pct = f"{st.gpu_share * 100:.0f} % GPU" if st.gpu_share is not None else "GPU-Anteil unbekannt"
    return ComponentHealth("ok", f"{st.model} geladen ({gpu_pct}, {st.context_length or settings.LOCAL_LLM_NUM_CTX} Tokens Kontext).", details=details)


def check_gpu(st: LLMStatus, settings: Settings) -> ComponentHealth:
    gpu = st.gpu or {}
    details = dict(gpu)
    adapter = gpu.get("adapter") or "GPU"
    if st.loaded and st.gpu_share is not None and st.gpu_share < 0.99:
        cpu_pct = (1 - st.gpu_share) * 100
        return ComponentHealth(
            "warn",
            f"{cpu_pct:.0f} % des Modells laufen auf der CPU statt auf der {adapter}.",
            "Zu wenig freier Grafikspeicher. Schliesse GPU-lastige Programme oder stelle LOCAL_LLM_MODEL auf das kleinere Fallback-Modell.",
            details,
        )
    spill = st.spill_gb
    if st.loaded and spill is not None and spill > settings.GPU_SHARED_SPILL_WARN_GB:
        return ComponentHealth(
            "warn",
            f"{_de(spill)} GB des Modells wurden in den Arbeitsspeicher ausgelagert – Antworten sind deutlich langsamer.",
            "Windows lagert aus, wenn der Grafikspeicher voll ist. Schliesse Spiele/Browser-Tabs mit Grafiklast oder nutze das kleinere Modell.",
            details,
        )
    if not gpu:
        if st.loaded and st.gpu_share is not None:
            return ComponentHealth("ok", f"Modell zu {st.gpu_share * 100:.0f} % auf der GPU (Auslagerung nicht messbar auf diesem System).", details=details)
        return ComponentHealth("unknown", "GPU-Speicher auf diesem System nicht messbar.", details=details)
    used, total = gpu.get("dedicated_used_gb"), gpu.get("vram_total_gb")
    usage = f"{_de(used)} / {_de(total)} GB belegt" if used is not None and total else "Belegung unbekannt"
    if not st.loaded:
        return ComponentHealth("ok", f"{adapter}: {usage}, kein Cypher-Modell geladen.", details=details)
    return ComponentHealth("ok", f"{adapter}: Modell komplett im Grafikspeicher, {usage}.", details=details)


def _check_warmup(component: ComponentWarmup, label: str, loaded: bool, ready_msg: str, details: Dict[str, Any]) -> ComponentHealth:
    details = {**details, "warmup": component.state.value, "warmup_seconds": component.seconds, "loaded": loaded}
    if component.state == WarmupState.FAILED:
        return ComponentHealth("error", f"{label} konnte nicht geladen werden.", "Details auf der Diagnose-Seite (Logs).", {**details, "error": component.error})
    if component.state in (WarmupState.RUNNING, WarmupState.PENDING):
        return ComponentHealth("warn", f"{label} wird geladen …", "Dauert beim ersten Start einige Sekunden.", details)
    if loaded:
        return ComponentHealth("ok", ready_msg, details=details)
    return ComponentHealth("ok", f"{label} wird beim ersten Einsatz geladen.", details=details)


def check_stt(runtime: Runtime) -> ComponentHealth:
    from app.stt import whisper_provider  # noqa: PLC0415

    s = runtime.settings
    details = {"model": s.WHISPER_MODEL_SIZE, "device": s.WHISPER_DEVICE, "compute_type": s.WHISPER_COMPUTE_TYPE, "threads": s.WHISPER_CPU_THREADS, "language": s.WHISPER_LANGUAGE or "auto"}
    return _check_warmup(runtime.stt, "Die Spracherkennung", whisper_provider.is_loaded(s), f"Spracherkennung bereit (Whisper {s.WHISPER_MODEL_SIZE}, CPU).", details)


def check_tts(runtime: Runtime) -> ComponentHealth:
    from app.tts import piper_provider  # noqa: PLC0415

    s = runtime.settings
    details = {"voice_dir": s.PIPER_MODEL_DIR, "threads": s.PIPER_NUM_THREADS}
    if not piper_provider.voice_files_present(s):
        return _from_error(errors.tts_voice_missing(), **details)
    return _check_warmup(runtime.tts, "Die Sprachausgabe", piper_provider.is_loaded(s), "Sprachausgabe bereit (Piper, Stimme Thorsten).", details)


async def collect_health(runtime: Runtime) -> Dict[str, Any]:
    settings = runtime.settings
    llm_status, db = await asyncio.gather(runtime.model_manager.status(), check_database())
    components: Dict[str, ComponentHealth] = {
        "database": db,
        "ollama": check_ollama(llm_status, settings),
        "llm_model": check_llm_model(llm_status, settings),
        "gpu": check_gpu(llm_status, settings),
        "stt": check_stt(runtime),
        "tts": check_tts(runtime),
    }
    if any(components[c].status == "error" for c in CORE_COMPONENTS):
        overall = "error"
    elif any(c.status in ("error", "warn") for c in components.values()):
        overall = "degraded"
    else:
        overall = "ok"
    return {"status": overall, "components": components, "llm": llm_status}


def public_report(report: Dict[str, Any], runtime: Runtime) -> Dict[str, Any]:
    return {
        "status": report["status"],
        "version": APP_VERSION,
        "uptime_seconds": round(time.time() - runtime.started_at),
        "components": {name: c.public() for name, c in report["components"].items()},
    }


def diagnostics_report(report: Dict[str, Any], runtime: Runtime) -> Dict[str, Any]:
    from app.core.metrics import metrics  # noqa: PLC0415

    s = runtime.settings
    return {
        **public_report(report, runtime),
        "component_details": {name: c.details for name, c in report["components"].items()},
        "latency": metrics.snapshot(),
        "config": {
            "llm_model": s.LOCAL_LLM_MODEL,
            "llm_fallback_model": s.LOCAL_LLM_FALLBACK_MODEL,
            "num_ctx": s.LOCAL_LLM_NUM_CTX,
            "keep_alive": s.LOCAL_LLM_KEEP_ALIVE,
            "think": s.LOCAL_LLM_THINK,
            "single_model_policy": s.LLM_SINGLE_MODEL_POLICY,
            "whisper": {"model": s.WHISPER_MODEL_SIZE, "threads": s.WHISPER_CPU_THREADS, "beam": s.WHISPER_BEAM_SIZE, "language": s.WHISPER_LANGUAGE},
            "api_host": s.API_HOST,
            "cloud_configured": bool(s.CLOUD_LLM_API_KEY),
        },
        "system": {"python": sys.version.split()[0], "platform": platform.platform()},
    }


def llm_status_dict(st: Optional[LLMStatus]) -> Dict[str, Any]:
    return asdict(st) if st else {}
