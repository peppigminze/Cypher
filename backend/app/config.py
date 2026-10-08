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

    # --- Tools -----------------------------------------------------------
    TOOL_TIMEOUT_SECONDS: float = Field(30.0, gt=0, le=600)
    # The LLM sees a curated default set; add more by name or hide some.
    TOOLS_EXTRA: str = ""
    TOOLS_DISABLED: str = ""
    FILE_READ_MAX_BYTES: int = Field(200_000, ge=1_000, le=20_000_000)
    FILE_WRITE_MAX_BYTES: int = Field(1_000_000, ge=1_000, le=50_000_000)
    WEB_SEARCH_PROVIDER: Literal["duckduckgo", "searxng", "off"] = "duckduckgo"
    SEARXNG_URL: str = ""
    WEB_SEARCH_MAX_RESULTS: int = Field(5, ge=1, le=20)
    # Characters of page text handed to the LLM (the context window is small).
    FETCH_MAX_CHARS: int = Field(6000, ge=500, le=100_000)
    # Apps open_app may start: name=target pairs. Target is an .exe name/path,
    # a URI (spotify:, ms-settings:) or a URL. Nothing else can be launched.
    APP_WHITELIST: str = (
        "rechner=calc.exe,taschenrechner=calc.exe,editor=notepad.exe,notepad=notepad.exe,"
        "explorer=explorer.exe,spotify=spotify:,browser=https://duckduckgo.com,einstellungen=ms-settings:"
    )
    WEATHER_DEFAULT_LOCATION: str = ""
    # Screen description via the local vision model (qwen3.5 can see). Images
    # never leave the PC: the tool refuses while the cloud provider is active.
    VISION_ENABLED: bool = True
    VOLUME_STEP_PERCENT: int = Field(10, ge=1, le=50)
    # Empty = DATA_DIR/notes
    NOTES_DIR: str = ""

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

    # --- Voice session (M3) -----------------------------------------------
    # wake = always listening for the wake word; auto = tap, stops on silence;
    # ptt = push-to-talk (hold/tap to start and stop).
    VOICE_DEFAULT_MODE: Literal["wake", "auto", "ptt"] = "auto"
    WAKE_WORD_ENABLED: bool = True
    WAKE_WORD_PHRASE: str = "Hey Cypher"
    # Model for the wake check; the command itself uses WHISPER_MODEL_SIZE.
    # "tiny" won the M3 test (6/7 detections, 0.19 s) - "small" transcribes too
    # literally ("heiße Eifer") and takes 1.1 s.
    WAKE_WORD_MODEL: str = "tiny"
    # Fuzzy-match score (0..1) needed to accept the wake word.
    # 0.85 accepts Whisper's usual mishearings ("Saifer" 0.91) but rejects
    # near-misses like "super" (0.80) or the name "Seifert" (0.83).
    WAKE_WORD_THRESHOLD: float = Field(0.85, ge=0.3, le=1.0)
    # Only the start of an utterance is checked for the wake word.
    WAKE_WORD_WINDOW_SECONDS: float = Field(2.5, ge=1.0, le=6.0)
    VAD_THRESHOLD: float = Field(0.5, ge=0.1, le=0.95)
    # Pause that ends an utterance. Shorter = faster answers, but cuts off slow speakers.
    VAD_SILENCE_MS: int = Field(700, ge=200, le=3000)
    # Start transcribing speculatively after this much silence; if the speaker
    # continues, the result is discarded. Hides ~0.45 s of STT time. 0 = off.
    VAD_EARLY_PAUSE_MS: int = Field(250, ge=0, le=2000)
    VAD_MIN_SPEECH_MS: int = Field(250, ge=50, le=2000)
    VAD_PREROLL_MS: int = Field(300, ge=0, le=1000)
    VAD_MAX_UTTERANCE_SECONDS: float = Field(20.0, ge=3, le=120)
    # After the wake word: how long to wait for the actual command.
    LISTEN_TIMEOUT_SECONDS: float = Field(8.0, ge=2, le=60)
    # aec_text: browser echo cancellation + ignore wake words that match what
    #           Cypher is saying right now (barge-in by voice possible)
    # mic_gate: ignore the microphone entirely while Cypher speaks (barge-in by tap only)
    ECHO_GUARD: Literal["aec_text", "mic_gate"] = "aec_text"
    # Short-term conversation memory handed to the LLM (rough token estimate).
    HISTORY_MAX_TOKENS: int = Field(1500, ge=200, le=6000)

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

    # --- Memory (M4) -----------------------------------------------------
    # Core memory (data/core_memory.json) is always in the system prompt:
    # this caps its size. Facts beyond it go to the searchable archive.
    MEMORY_CORE_MAX_TOKENS: int = Field(400, ge=50, le=1500)
    # Archive entries matching the current question are added to that turn,
    # up to this many tokens (0 = never).
    MEMORY_RECALL_MAX_TOKENS: int = Field(200, ge=0, le=800)
    # A reconnecting voice client continues its last conversation if it was
    # active within this many minutes; otherwise a new one starts.
    CONVERSATION_RESUME_MINUTES: int = Field(30, ge=0, le=1440)
    # Stored conversation history older than this is deleted at startup.
    CONVERSATION_KEEP_DAYS: int = Field(30, ge=1, le=3650)

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

    @field_validator("APP_WHITELIST")
    @classmethod
    def _check_whitelist(cls, v: str) -> str:
        for pair in [p for p in v.split(",") if p.strip()]:
            if "=" not in pair or not pair.split("=", 1)[0].strip() or not pair.split("=", 1)[1].strip():
                raise ValueError(f"Eintrag '{pair.strip()}' muss die Form name=ziel haben (z. B. rechner=calc.exe)")
        return v

    @field_validator("SEARXNG_URL")
    @classmethod
    def _check_searxng(cls, v: str) -> str:
        if v and not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("muss mit http:// oder https:// beginnen")
        return v.rstrip("/")

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
    def tools_extra_list(self) -> List[str]:
        return [t.strip() for t in self.TOOLS_EXTRA.split(",") if t.strip()]

    @property
    def tools_disabled_list(self) -> List[str]:
        return [t.strip() for t in self.TOOLS_DISABLED.split(",") if t.strip()]

    @property
    def app_whitelist_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for pair in self.APP_WHITELIST.split(","):
            if "=" in pair:
                name, target = pair.split("=", 1)
                if name.strip() and target.strip():
                    out[name.strip().lower()] = target.strip()
        return out

    @property
    def notes_dir_path(self) -> Path:
        return _resolve_project_path(self.NOTES_DIR) if self.NOTES_DIR.strip() else self.data_dir_path / "notes"

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
