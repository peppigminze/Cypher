from __future__ import annotations

from typing import Optional

from app.config import Settings, get_settings
from app.llm.base import LLMProvider, LLMUnavailableError
from app.llm.cloud_provider import CloudLLMProvider
from app.llm.ollama import OllamaProvider


def build_local_provider(settings: Settings | None = None, model: str | None = None) -> OllamaProvider:
    """The local Ollama provider with every tuning knob from config."""
    settings = settings or get_settings()
    return OllamaProvider(
        base_url=settings.LOCAL_LLM_BASE_URL,
        model=model or settings.LOCAL_LLM_MODEL,
        timeout_seconds=settings.LOCAL_LLM_TIMEOUT_SECONDS,
        connect_timeout_seconds=settings.LOCAL_LLM_CONNECT_TIMEOUT_SECONDS,
        num_ctx=settings.LOCAL_LLM_NUM_CTX,
        keep_alive=settings.LOCAL_LLM_KEEP_ALIVE,
        think=settings.LOCAL_LLM_THINK,
        temperature=settings.LLM_TEMPERATURE,
    )


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """The primary provider of the legacy /api/messages pipeline - always local."""
    settings = settings or get_settings()
    if settings.LLM_PROVIDER == "local":
        return build_local_provider(settings)
    raise ValueError(f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'. Only 'local' is supported.")


def get_cloud_llm_provider(settings: Settings | None = None) -> Optional[LLMProvider]:
    """Legacy automatic cloud fallback of the old pipeline: only when
    CLOUD_LLM_ENABLED=true AND a key is configured. Removed together with
    that pipeline in M5 (see docs/overhaul/AUDIT.md section 9)."""
    settings = settings or get_settings()
    if not settings.CLOUD_LLM_ENABLED or not settings.CLOUD_LLM_API_KEY:
        return None
    return CloudLLMProvider(
        base_url=settings.CLOUD_LLM_BASE_URL,
        model=settings.CLOUD_LLM_MODEL,
        api_key=settings.CLOUD_LLM_API_KEY,
        timeout_seconds=settings.CLOUD_LLM_TIMEOUT_SECONDS,
    )


def get_provider_by_name(provider_name: str, settings: Settings | None = None) -> LLMProvider:
    """Provider for an explicit runtime choice ("local"/"cloud").

    Never falls back silently: if "cloud" is selected without a key,
    this raises so the user learns why, instead of getting a local
    answer they didn't ask for.
    """
    settings = settings or get_settings()

    if provider_name == "local":
        return build_local_provider(settings)

    if provider_name == "cloud":
        if not settings.CLOUD_LLM_API_KEY:
            raise LLMUnavailableError(
                "Cloud provider is selected, but CLOUD_LLM_API_KEY is not set - add it to .env.",
                provider="cloud",
            )
        return CloudLLMProvider(
            base_url=settings.CLOUD_LLM_BASE_URL,
            model=settings.CLOUD_LLM_MODEL,
            api_key=settings.CLOUD_LLM_API_KEY,
            timeout_seconds=settings.CLOUD_LLM_TIMEOUT_SECONDS,
        )

    raise ValueError(f"Unknown provider '{provider_name}'. Must be 'local' or 'cloud'.")
