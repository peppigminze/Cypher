from __future__ import annotations

from typing import Optional

from app.config import Settings, get_settings
from app.llm.base import LLMProvider, LLMUnavailableError
from app.llm.cloud_provider import CloudLLMProvider
from app.llm.local_provider import LocalLLMProvider


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """
    Returns the *primary* LLMProvider - "local" for V1 (project spec:
    LOCAL FIRST, always). The branch structure below is where future
    primary providers would plug in based on LLM_PROVIDER, without any
    change needed in the Agent Core. Cloud is never the primary
    provider - see get_cloud_llm_provider() for the opt-in fallback.
    """
    settings = settings or get_settings()

    if settings.LLM_PROVIDER == "local":
        return LocalLLMProvider(
            base_url=settings.LOCAL_LLM_BASE_URL,
            model=settings.LOCAL_LLM_MODEL,
            timeout_seconds=settings.LOCAL_LLM_TIMEOUT_SECONDS,
        )

    # Future:
    # if settings.LLM_PROVIDER == "openai":
    #     return OpenAIProvider(...)
    # if settings.LLM_PROVIDER == "anthropic":
    #     return AnthropicProvider(...)

    raise ValueError(
        f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'. "
        "V1 only supports 'local'."
    )


def get_cloud_llm_provider(settings: Settings | None = None) -> Optional[LLMProvider]:
    """
    Returns the optional cloud fallback provider, or None if it's
    disabled or has no API key configured (project spec sections 8/9:
    cloud is opt-in and never required to use local Cypher). The Agent
    Core only ever calls this when the local LLM itself raised
    LLMUnavailableError - never as a first choice, so there is no
    surprise cloud spend.
    """
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
    """Construct the LLMProvider for an explicit "local"/"cloud" choice -
    the native tool-calling pipeline's runtime toggle (Phase 7, see
    app/settings_store.py), NOT the OLD /api/messages pipeline's
    static-primary + automatic-fallback behavior served by
    get_llm_provider()/get_cloud_llm_provider() above.

    Always honors the caller's explicit choice: if "cloud" is selected
    but no API key is configured, this raises instead of silently
    falling back to local - a user who deliberately switched providers
    should be told why it didn't work, not served a local answer they
    didn't ask for.
    """
    settings = settings or get_settings()

    if provider_name == "local":
        return LocalLLMProvider(
            base_url=settings.LOCAL_LLM_BASE_URL,
            model=settings.LOCAL_LLM_MODEL,
            timeout_seconds=settings.LOCAL_LLM_TIMEOUT_SECONDS,
        )

    if provider_name == "cloud":
        if not settings.CLOUD_LLM_API_KEY:
            raise LLMUnavailableError(
                "Cloud provider is selected, but CLOUD_LLM_API_KEY is not set - add it to .env."
            )
        return CloudLLMProvider(
            base_url=settings.CLOUD_LLM_BASE_URL,
            model=settings.CLOUD_LLM_MODEL,
            api_key=settings.CLOUD_LLM_API_KEY,
            timeout_seconds=settings.CLOUD_LLM_TIMEOUT_SECONDS,
        )

    raise ValueError(f"Unknown provider '{provider_name}'. Must be 'local' or 'cloud'.")
