"""
Builds LLM providers from configuration - the only place that knows which
class is behind "local" and "cloud".

Cloud rules (M5):
  * usable only if CLOUD_LLM_ENABLED=true AND CLOUD_LLM_API_KEY is set
    (cloud_status) - otherwise a clear German error, never a silent
    local answer the user didn't ask for, and never a silent cloud answer;
  * refused once this month's spending reaches CLOUD_MONTHLY_BUDGET_USD.
"""
from __future__ import annotations

from typing import Optional

from app.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.cloud_provider import CloudBudgetExceeded, CloudLLMProvider, CloudNotConfigured
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


def cloud_status(settings: Settings | None = None) -> str:
    """"ok", "disabled" (CLOUD_LLM_ENABLED=false) or "no_key"."""
    settings = settings or get_settings()
    if not settings.CLOUD_LLM_ENABLED:
        return "disabled"
    if not settings.CLOUD_LLM_API_KEY:
        return "no_key"
    return "ok"


def monthly_cloud_spend_usd() -> float:
    from app.database.db import SessionLocal  # noqa: PLC0415
    from app.settings_store import month_cost_usd  # noqa: PLC0415

    with SessionLocal() as db:
        return month_cost_usd(db, provider="cloud")


def build_cloud_provider(settings: Settings | None = None, *, check_budget: bool = True) -> CloudLLMProvider:
    settings = settings or get_settings()
    if cloud_status(settings) != "ok":
        raise CloudNotConfigured("Cloud is disabled or has no API key.", provider="cloud")
    budget = settings.CLOUD_MONTHLY_BUDGET_USD
    if check_budget and budget > 0:
        spent = monthly_cloud_spend_usd()
        if spent >= budget:
            raise CloudBudgetExceeded(spent, budget)
    return CloudLLMProvider(
        base_url=settings.CLOUD_LLM_BASE_URL,
        model=settings.CLOUD_LLM_MODEL,
        api_key=settings.CLOUD_LLM_API_KEY,
        timeout_seconds=settings.CLOUD_LLM_TIMEOUT_SECONDS,
        reasoning_effort=settings.CLOUD_LLM_REASONING_EFFORT,
    )


def get_provider_by_name(provider_name: str, settings: Settings | None = None) -> LLMProvider:
    """Provider for an explicit choice ("local"/"cloud"). Never falls back."""
    settings = settings or get_settings()
    if provider_name == "local":
        return build_local_provider(settings)
    if provider_name == "cloud":
        return build_cloud_provider(settings)
    raise ValueError(f"Unknown provider '{provider_name}'. Must be 'local' or 'cloud'.")


def cloud_available(settings: Optional[Settings] = None) -> bool:
    return cloud_status(settings) == "ok"
