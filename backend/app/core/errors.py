"""
Error catalog: every failure the user can run into maps to a short
German message (UI + speakable via TTS, so no paths/stack traces) and a
concrete next step ("hint").

API responses keep FastAPI's `detail` field (the old frontend reads it)
and add a structured `error` object:

    {"detail": "<message>", "error": {"code", "message", "hint", "retryable", "request_id"}}
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.context import current_request_id

logger = logging.getLogger("cypher.errors")


@dataclass
class CypherError(Exception):
    code: str
    message: str
    hint: str = ""
    http_status: int = 500
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
            "request_id": current_request_id(),
        }

    @property
    def spoken(self) -> str:
        """Text suitable for TTS: message only, hints are for the screen."""
        return self.message


# ---------------------------------------------------------------- catalog
def ollama_unreachable(base_url: str) -> CypherError:
    return CypherError(
        code="ollama_unreachable",
        message="Ich erreiche mein Sprachmodell gerade nicht.",
        hint=f"Ollama läuft nicht oder ist unter {base_url} nicht erreichbar. Starte Ollama (Startmenü oder start_cypher.bat) – Cypher verbindet sich danach automatisch neu.",
        http_status=503,
        retryable=True,
    )


def model_missing(model: str) -> CypherError:
    return CypherError(
        code="model_missing",
        message="Das eingestellte Sprachmodell ist nicht installiert.",
        hint=f"Installiere es mit: ollama pull {model}  (oder ändere LOCAL_LLM_MODEL in der .env).",
        http_status=503,
        details={"model": model},
    )


def llm_timeout(seconds: float) -> CypherError:
    return CypherError(
        code="llm_timeout",
        message="Das Sprachmodell hat zu lange gebraucht.",
        hint=f"Keine Antwort innerhalb von {seconds:.0f} s. Läuft gerade etwas anderes auf der Grafikkarte? Ein Blick auf die Diagnose-Seite zeigt, ob das Modell ausgelagert wird.",
        http_status=504,
        retryable=True,
    )


def llm_bad_response(reason: str = "") -> CypherError:
    return CypherError(
        code="llm_bad_response",
        message="Das Sprachmodell hat unverständlich geantwortet.",
        hint="Versuch es noch einmal. Wenn es wiederholt passiert, steht der Grund im Log (Diagnose → Logs)." + (f" Detail: {reason}" if reason else ""),
        http_status=502,
        retryable=True,
    )


def cloud_not_configured() -> CypherError:
    return CypherError(
        code="cloud_not_configured",
        message="Der Cloud-Modus ist nicht eingerichtet.",
        hint="In der .env CLOUD_LLM_ENABLED=true und CLOUD_LLM_API_KEY setzen und Cypher neu starten – oder beim lokalen Modell bleiben.",
        http_status=400,
    )


def cloud_unavailable() -> CypherError:
    return CypherError(
        code="cloud_unavailable",
        message="Das Cloud-Modell ist gerade nicht erreichbar.",
        hint="Prüfe Internetverbindung und API-Key, oder schalte in den Einstellungen zurück auf Lokal.",
        http_status=503,
        retryable=True,
    )


def cloud_auth_failed() -> CypherError:
    return CypherError(
        code="cloud_auth_failed",
        message="Der Cloud-Anbieter hat den API-Schlüssel abgelehnt.",
        hint="Prüfe CLOUD_LLM_API_KEY in der .env (ohne Leerzeichen) und starte Cypher neu.",
        http_status=401,
    )


def cloud_rate_limited() -> CypherError:
    return CypherError(
        code="cloud_rate_limited",
        message="Beim Cloud-Anbieter ist das Limit erreicht.",
        hint="Kurz warten oder Guthaben/Limits im Konto des Anbieters prüfen. Lokal geht es weiter.",
        http_status=429,
        retryable=True,
    )


def cloud_budget_exceeded(spent_usd: float, budget_usd: float) -> CypherError:
    return CypherError(
        code="cloud_budget_exceeded",
        message=f"Das Cloud-Budget für diesen Monat ist aufgebraucht ({spent_usd:.2f} von {budget_usd:.2f} USD).",
        hint="Erhöhe CLOUD_MONTHLY_BUDGET_USD in der .env oder bleib beim lokalen Modell.",
        http_status=402,
    )


def audio_invalid(reason: str) -> CypherError:
    return CypherError(code="audio_invalid", message="Die Aufnahme war leer oder unlesbar.", hint=reason, http_status=400)


def stt_failed() -> CypherError:
    return CypherError(
        code="stt_failed",
        message="Ich konnte die Aufnahme nicht in Text umwandeln.",
        hint="Versuch es noch einmal. Bleibt der Fehler, zeigt die Diagnose-Seite den Zustand der Spracherkennung.",
        http_status=500,
        retryable=True,
    )


def tts_voice_missing() -> CypherError:
    return CypherError(
        code="tts_voice_missing",
        message="Meine Stimme ist nicht installiert.",
        hint="Führe setup.bat aus (oder: python backend/scripts/download_piper_voice.py).",
        http_status=503,
    )


def tts_failed() -> CypherError:
    return CypherError(
        code="tts_failed",
        message="Die Sprachausgabe ist fehlgeschlagen.",
        hint="Die Antwort steht trotzdem als Text da. Details im Log (Diagnose → Logs).",
        http_status=500,
        retryable=True,
    )


def internal_error() -> CypherError:
    return CypherError(
        code="internal_error",
        message="Da ist intern etwas schiefgelaufen.",
        hint="Versuch es noch einmal. Die technische Ursache steht im Log (Diagnose → Logs) unter der angezeigten Request-ID.",
        http_status=500,
        retryable=True,
    )


# ---------------------------------------------------------------- mapping
def from_exception(exc: BaseException) -> CypherError:
    """Translate known lower-level exceptions into catalog errors."""
    if isinstance(exc, CypherError):
        return exc
    # Imported lazily: app.llm depends on app.core, not the other way round.
    from app.llm.base import (  # noqa: PLC0415
        LLMResponseError,
        LLMTimeoutError,
        LLMUnavailableError,
        ModelNotFoundError,
    )

    from app.llm.cloud_provider import CloudAuthError, CloudBudgetExceeded, CloudNotConfigured, CloudRateLimitError  # noqa: PLC0415

    if isinstance(exc, CloudNotConfigured):
        return cloud_not_configured()
    if isinstance(exc, CloudAuthError):
        return cloud_auth_failed()
    if isinstance(exc, CloudRateLimitError):
        return cloud_rate_limited()
    if isinstance(exc, CloudBudgetExceeded):
        return cloud_budget_exceeded(exc.spent_usd, exc.budget_usd)
    if isinstance(exc, ModelNotFoundError):
        return model_missing(exc.model)
    if isinstance(exc, LLMTimeoutError):
        return llm_timeout(exc.timeout_seconds)
    if isinstance(exc, LLMResponseError):
        return llm_bad_response(str(exc))
    if isinstance(exc, LLMUnavailableError):
        if getattr(exc, "provider", "local") == "cloud":
            return cloud_unavailable()
        return ollama_unreachable(getattr(exc, "base_url", "http://localhost:11434"))
    return internal_error()


def install_exception_handlers(app: FastAPI) -> None:
    from app.llm.base import LLMUnavailableError  # noqa: PLC0415

    async def _handle(request: Request, exc: Exception) -> JSONResponse:
        err = from_exception(exc)
        if err.code == "internal_error":
            logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        else:
            logger.warning("%s on %s %s: %s", err.code, request.method, request.url.path, exc)
        return JSONResponse(status_code=err.http_status, content={"detail": err.message, "error": err.to_dict()})

    app.add_exception_handler(CypherError, _handle)
    app.add_exception_handler(LLMUnavailableError, _handle)
    app.add_exception_handler(Exception, _handle)
