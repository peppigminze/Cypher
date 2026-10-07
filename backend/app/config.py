"""
Central, validated configuration for the Cypher backend.

Every value comes from environment variables or the ONE shared `.env`
in the project root (not backend/). Types, ranges and allowed values
are enforced at startup by pydantic-settings, so a typo like
`WHISPER_MODEL_SIZE=smal` fails immediately with a readable German
message instead of on the first voice request an hour later.

Backward compatibility: every variable name that existed before the
overhaul keeps its meaning. New variables all have safe defaults, so an
old `.env` keeps working unchanged.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import List, Literal

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> project root. The .env lives there because it
# is shared by every entry point (backend, scripts), so it must not be
# resolved against whatever working directory a process was started in.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
_PROJECT_ROOT_ENV = PROJECT_ROOT / ".env"

# Ollama accepts Go-style durations ("30m", "1h", "90s") or a number of
# seconds; "-1" keeps the model loaded forever, "0" unloads immediately.
_KEEP_ALIVE_RE = re.compile(r"^-?\d+(\.\d+)?(ms|s|m|h)?$")

_KNOWN_WHISPER_SIZES = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
    "large-v1", "large-v2", "large-v3", "large", "large-v3-turbo", "turbo",
    "distil-small.en", "distil-medium.en", "distil-large-v2", "distil-large-v3",
}


class ConfigError(Exception):
    """Raised when the configuration is invalid. `str()` is a German,
    human-readable list of every problem plus where to fix it."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT_ENV,
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    APP_ENV: Literal["development", "production", "test"] = "development"

    # --- Database --------------------------------------------------------
    DATABASE_URL: str = "sqlite:///./jarvis.db"

    # --- Local LLM (Ollama) ---------------------------------------------
    LLM_PROVIDER: Literal["local"] = "local"
    LOCAL_LLM_BASE_URL: str = "http://127.0.0.1:11434"
    LOCAL_LLM_MODEL: str = "qwen3.5:9b"
    # Smaller model offered when the default is missing or too slow.
    LOCAL_LLM_FALLBACK_MODEL: str = "qwen3.5:4b"
    # Read timeout for one LLM call (time between streamed chunks).
    LOCAL_LLM_TIMEOUT_SECONDS: float = Field(60.0, gt=0, le=600)
    LOCAL_LLM_CONNECT_TIMEOUT_SECONDS: float = Field(3.0, gt=0, le=60)
    # Context window sent with every request. Ollama's own default is
    # derived from free VRAM (4096 here), which silently truncates long
    # prompts - so Cypher always sets it explicitly. See docs/overhaul/AUDIT.md H2.
    LOCAL_LLM_NUM_CTX: int = Field(8192, ge=2048, le=131072)
    # How long Ollama keeps the model in VRAM after the last request.
    # Ollama's default (5m) caused 7-14 s cold starts after short pauses (AUDIT K2).
    LOCAL_LLM_KEEP_ALIVE: str = "30m"
    # Hybrid "thinking" models (Qwen 3.5) reason by default, which costs
    # 5-30 s per answer - far too slow for voice. Off unless explicitly wanted.
    LOCAL_LLM_THINK: bool = False
    LLM_TEMPERATURE: float = Field(0.3, ge=0.0, le=2.0)
    LLM_WARMUP_ON_START: bool = True
    # How often the background monitor checks Ollama (and re-warms the model
    # after Ollama was restarted). 0 disables the monitor (tests).
    LLM_MONITOR_INTERVAL_SECONDS: float = Field(15.0, ge=0, le=3600)
    # Unload every other Ollama model when Cypher loads its own. Windows
    # silently spills overflowing VRAM into shared system RAM (10-20x
    # slower, AUDIT K1), so only one chat model may be resident.
    LLM_SINGLE_MODEL_POLICY: bool = True
    # Health turns "degraded" once this much of the LLM lives in shared memory.
    GPU_SHARED_SPILL_WARN_GB: float = Field(0.5, ge=0)
    # Agent loop limits.
    AGENT_MAX_STEPS: int = Field(5, ge=1, le=20)
    APPROVAL_TTL_SECONDS: int = Field(300, ge=10, le=3600)

    # --- API server ------------------------------------------------------
    # Read by backend/run.py. Default is localhost-only on purpose:
    # widening it (Tailscale IP, 0.0.0.0) is an explicit opt-in.
    API_HOST: str = "127.0.0.1"
    API_PORT: int = Field(8000, ge=1, le=65535)
    # Exact origins only - never "*" (any website could then call the API
    # with a visitor's credentials).
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Auth ------------------------------------------------------------
    USER_TOKEN: str = "change-me-user-token"
    AGENT_TOKEN: str = "change-me-agent-token"

    # --- Legacy PC agent process (agent/run_agent.py) ---------------------
    BACKEND_BASE_URL: str = "http://localhost:8000"
    AGENT_POLL_INTERVAL_SECONDS: float = Field(5.0, gt=0)
    AGENT_HEARTBEAT_INTERVAL_SECONDS: float = Field(15.0, gt=0)

    # --- PC control tools -----------------------------------------------
    ALLOWED_DIRECTORIES: str = ""
    TERMINAL_TIMEOUT_SECONDS: float = Field(30.0, gt=0, le=600)
    SEARCH_DIRECTORIES: str = ""
    SEARCH_MAX_RESULTS: int = Field(50, ge=1, le=1000)

    # --- Time ------------------------------------------------------------
    # IANA name like "Europe/Zurich". Empty = the PC's own local time zone.
    TIMEZONE: str = ""

    # --- Wake-on-LAN (informational) ------------------------------------
    WAKE_ON_LAN_MAC: str = ""
    WAKE_ON_LAN_BROADCAST: str = "255.255.255.255"
    WAKE_ON_LAN_PORT: int = Field(9, ge=1, le=65535)

    # --- Sync/message retries (legacy pipeline) --------------------------
    MESSAGE_MAX_RETRIES: int = Field(3, ge=0, le=20)
    MESSAGE_RETRY_BACKOFF_SECONDS: float = Field(15.0, ge=0)

    # --- Cloud LLM (optional, never automatic) ---------------------------
    CLOUD_LLM_ENABLED: bool = False
    CLOUD_LLM_BASE_URL: str = "https://api.openai.com/v1"
    CLOUD_LLM_MODEL: str = "gpt-5.6-luna"
    CLOUD_LLM_API_KEY: str = ""
    CLOUD_LLM_TIMEOUT_SECONDS: float = Field(30.0, gt=0, le=600)

    # --- Speech-to-text (faster-whisper, CPU) ----------------------------
    # "small" won the M0 benchmark (WER ~13-19 % vs ~37 % for "base", +0.7 s).
    WHISPER_MODEL_SIZE: str = "small"
    WHISPER_DEVICE: Literal["cpu", "cuda", "auto"] = "cpu"
    WHISPER_COMPUTE_TYPE: Literal["int8", "int8_float32", "int8_float16", "int16", "float16", "float32", "default"] = "int8"
    # More than 8 threads gave no speedup in the M0 benchmark.
    WHISPER_CPU_THREADS: int = Field(8, ge=0, le=64)
    WHISPER_BEAM_SIZE: int = Field(5, ge=1, le=10)
    # Fixed language avoids per-request language detection, which is slow
    # and unreliable for 1-2 second commands. Empty = auto-detect.
    WHISPER_LANGUAGE: str = "de"
    # Biases recognition towards proper nouns ("Cypher" instead of "Süfer").
    WHISPER_INITIAL_PROMPT: str = "Hey Cypher."
    WHISPER_WARMUP_ON_START: bool = True

    # --- Text-to-speech (Piper voice via sherpa-onnx) --------------------
    PIPER_MODEL_DIR: str = "models/piper/de_DE-thorsten-medium"
    PIPER_NUM_THREADS: int = Field(2, ge=1, le=32)
    PIPER_SPEAKER_ID: int = Field(0, ge=0)
    PIPER_SPEED: float = Field(1.0, gt=0.3, le=3.0)
    TTS_WARMUP_ON_START: bool = True

    # --- Logging ---------------------------------------------------------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Relative paths resolve against the project root.
    LOG_DIR: str = "logs"
    LOG_FILE_MAX_BYTES: int = Field(5_000_000, ge=10_000)
    LOG_FILE_BACKUPS: int = Field(5, ge=0, le=100)
    # One JSON object per line in the log file (console stays human-readable).
    LOG_JSON: bool = True

    # --- Local data (memory, notes, secrets, backups) --------------------
    DATA_DIR: str = "data"

    # ------------------------------------------------------------------ validators
    @field_validator("DATABASE_URL")
    @classmethod
    def _check_database_url(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9+]+://", v):
            raise ValueError("muss eine SQLAlchemy-URL sein, z. B. sqlite:///./jarvis.db")
        return v

    @field_validator("LOCAL_LLM_BASE_URL", "CLOUD_LLM_BASE_URL", "BACKEND_BASE_URL")
    @classmethod
    def _check_http_url(cls, v: str) -> str:
        if not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("muss mit http:// oder https:// beginnen")
        return v.rstrip("/")

    @field_validator("LOCAL_LLM_MODEL", "LOCAL_LLM_FALLBACK_MODEL")
    @classmethod
    def _check_model_name(cls, v: str) -> str:
        v = v.strip()
        if not v or " " in v:
            raise ValueError("Modellname darf nicht leer sein und keine Leerzeichen enthalten (z. B. qwen3.5:9b)")
        return v

    @field_validator("LOCAL_LLM_KEEP_ALIVE")
    @classmethod
    def _check_keep_alive(cls, v: str) -> str:
        v = v.strip()
        if not _KEEP_ALIVE_RE.match(v):
            raise ValueError("Dauer wie 30m, 1h, 90s oder -1 (für immer)")
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _check_cors(cls, v: str) -> str:
        origins = [o.strip() for o in v.split(",") if o.strip()]
        if any(o == "*" for o in origins):
            raise ValueError('"*" ist nicht erlaubt - trage die genauen Adressen des Frontends ein')
        for o in origins:
            if not re.match(r"^https?://", o):
                raise ValueError(f"'{o}' ist keine gültige Origin (muss mit http:// oder https:// beginnen)")
        return v

    @field_validator("WHISPER_MODEL_SIZE")
    @classmethod
    def _check_whisper_size(cls, v: str) -> str:
        v = v.strip()
        if v in _KNOWN_WHISPER_SIZES or Path(v).is_dir() or "/" in v:
            # "/" allows Hugging Face repo ids like "Systran/faster-whisper-small".
            return v
        raise ValueError(f"unbekannte Modellgröße - erlaubt sind z. B. {', '.join(sorted(['tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo']))}")

    @field_validator("TIMEZONE")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v
        try:
            from zoneinfo import ZoneInfo  # noqa: PLC0415

            ZoneInfo(v)
        except Exception as exc:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError, missing tzdata
            raise ValueError(f"unbekannte Zeitzone ({exc.__class__.__name__}) - z. B. Europe/Zurich, oder leer lassen für die PC-Zeitzone") from exc
        return v

    @model_validator(mode="after")
    def _check_paths(self) -> "Settings":
        for name in ("LOG_DIR", "DATA_DIR"):
            raw = getattr(self, name)
            if not raw.strip():
                raise ValueError(f"{name} darf nicht leer sein")
        return self

    # ------------------------------------------------------------------ derived values
    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_directories_list(self) -> List[str]:
        return [d.strip() for d in self.ALLOWED_DIRECTORIES.split(",") if d.strip()]

    @property
    def search_directories_list(self) -> List[str]:
        dirs = [d.strip() for d in self.SEARCH_DIRECTORIES.split(",") if d.strip()]
        return dirs or [str(Path.home())]

    @property
    def log_dir_path(self) -> Path:
        return _resolve_project_path(self.LOG_DIR)

    @property
    def data_dir_path(self) -> Path:
        return _resolve_project_path(self.DATA_DIR)

    @property
    def is_localhost_only(self) -> bool:
        return self.API_HOST in ("127.0.0.1", "localhost", "::1")


def _resolve_project_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def format_validation_error(exc: ValidationError) -> str:
    """Turn a pydantic ValidationError into a German, actionable message."""
    lines = ["Die Konfiguration ist ungültig. Bitte in der Datei", f"  {_PROJECT_ROOT_ENV}", "korrigieren:"]
    for err in exc.errors():
        field = ".".join(str(p) for p in err.get("loc", ())) or "(allgemein)"
        msg = err.get("msg", "")
        # pydantic prefixes custom ValueErrors with "Value error, ".
        msg = msg.removeprefix("Value error, ")
        given = err.get("input")
        given_txt = f" (aktuell: {given!r})" if given is not None and not isinstance(given, dict) and "TOKEN" not in field and "KEY" not in field else ""
        lines.append(f"  - {field}: {msg}{given_txt}")
    lines.append("Vorlage mit allen Variablen und Erklärungen: .env.example")
    return "\n".join(lines)


@lru_cache
def get_settings() -> Settings:
    """Settings are cached so the .env file is only parsed once.

    Raises ConfigError (German message) instead of a raw pydantic
    ValidationError so run.py / the doctor script can print it as-is.
    """
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from exc
