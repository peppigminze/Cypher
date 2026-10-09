"""
Runtime-mutable settings + token usage tracking for the native
tool-calling pipeline (Phase 7) - persisted in SQLite (RuntimeSetting /
TokenUsageDaily, app/database/models.py) rather than app/config.py's
static, .env-only Settings, since the active provider must be
changeable at runtime (POST /api/settings/provider, no restart) and
survive one when it happens.

Deliberately scoped to the native tool-calling pipeline
(app/agent/native_tool_agent.py, app/api/tool_chat.py). The OLD
/api/messages pipeline (app/agent/core.py) keeps its own separate,
env-only CLOUD_LLM_ENABLED automatic-fallback behavior untouched - the
two are independent by design (see app/llm/cloud_provider.py's module
docstring).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.database.models import RuntimeSetting, TokenUsageDaily

ACTIVE_PROVIDER_KEY = "active_llm_provider"
DEFAULT_PROVIDER = "local"  # Phase 7 requirement: local is always the default on first start.
VALID_PROVIDERS = ("local", "cloud")


def get_active_provider(db: Session) -> str:
    """The provider the native tool-calling pipeline should use right
    now. Defaults to "local" if nothing has been persisted yet, or if
    the stored value is somehow no longer valid - never fails closed
    into an unusable state."""
    row = db.get(RuntimeSetting, ACTIVE_PROVIDER_KEY)
    if row is None or row.value not in VALID_PROVIDERS:
        return DEFAULT_PROVIDER
    return row.value


def set_active_provider(db: Session, provider: str) -> str:
    """Persist the chosen provider so it survives a backend restart.
    This is the ONLY way the active provider changes - always an
    explicit call from a human action (POST /api/settings/provider),
    never inferred or automatic (Phase 7 requirement: no auto-switch)."""
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Must be one of {VALID_PROVIDERS}.")
    row = db.get(RuntimeSetting, ACTIVE_PROVIDER_KEY)
    if row is None:
        db.add(RuntimeSetting(key=ACTIVE_PROVIDER_KEY, value=provider))
    else:
        row.value = provider
    db.commit()
    return provider


FOLLOWUP_WINDOW_KEY = "voice.followup_window_seconds"


def get_followup_window_seconds(db: Session, default: float) -> float:
    """Follow-up listening window (seconds, 0 = off): the menu's value if
    one was saved, otherwise FOLLOWUP_WINDOW_SECONDS from .env."""
    row = db.get(RuntimeSetting, FOLLOWUP_WINDOW_KEY)
    try:
        value = float(row.value) if row is not None else default
    except ValueError:
        return default
    return min(max(value, 0.0), 60.0)


def set_followup_window_seconds(db: Session, seconds: float) -> float:
    seconds = min(max(float(seconds), 0.0), 60.0)
    row = db.get(RuntimeSetting, FOLLOWUP_WINDOW_KEY)
    if row is None:
        db.add(RuntimeSetting(key=FOLLOWUP_WINDOW_KEY, value=str(seconds)))
    else:
        row.value = str(seconds)
    db.commit()
    return seconds


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_token_usage(db: Session, provider: str, model: str, usage: Optional[Dict[str, int]]) -> None:
    """Add one LLM call's token usage to today's running total for
    (provider, model). A no-op if `usage` is None - not every
    OpenAI-compatible backend reports it (local Ollama sometimes
    doesn't), and guessing a number would be worse than not counting."""
    if not usage:
        return

    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    cached = int(usage.get("cached_prompt_tokens") or 0)
    cost = 0.0
    if provider == "cloud":
        from app.config import get_settings  # noqa: PLC0415
        from app.llm.pricing import cost_usd  # noqa: PLC0415

        cost = cost_usd(model, prompt, cached, completion, get_settings()) or 0.0

    today = _today_utc()
    row = db.query(TokenUsageDaily).filter_by(date=today, provider=provider, model=model).one_or_none()
    if row is None:
        # Explicit zeros: the model's default=0 is a SQLAlchemy *insert*
        # default, only applied at flush time - accessing/incrementing
        # these attributes on the fresh, unflushed object below would
        # otherwise hit None instead of 0.
        row = TokenUsageDaily(
            date=today, provider=provider, model=model,
            prompt_tokens=0, completion_tokens=0, total_tokens=0, request_count=0,
            cached_prompt_tokens=0, cost_usd=0.0,
        )
        db.add(row)
    row.prompt_tokens += prompt
    row.completion_tokens += completion
    row.total_tokens += total
    row.request_count += 1
    row.cached_prompt_tokens = (row.cached_prompt_tokens or 0) + cached
    row.cost_usd = (row.cost_usd or 0.0) + cost
    db.commit()


def _sum_rows(rows) -> dict:
    return {
        "prompt_tokens": sum(r.prompt_tokens for r in rows),
        "completion_tokens": sum(r.completion_tokens for r in rows),
        "total_tokens": sum(r.total_tokens for r in rows),
        "request_count": sum(r.request_count for r in rows),
        "cost_usd": round(sum(r.cost_usd or 0.0 for r in rows), 6),
    }


def month_cost_usd(db: Session, provider: str = "cloud") -> float:
    """What the cloud cost this calendar month (UTC) - for the budget cap."""
    prefix = _today_utc()[:7]
    rows = db.query(TokenUsageDaily).filter(TokenUsageDaily.date.like(f"{prefix}%"), TokenUsageDaily.provider == provider).all()
    return sum(r.cost_usd or 0.0 for r in rows)


def _by_provider(rows) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in rows:
        bucket = out.setdefault(
            r.provider, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "request_count": 0, "cost_usd": 0.0}
        )
        bucket["prompt_tokens"] += r.prompt_tokens
        bucket["completion_tokens"] += r.completion_tokens
        bucket["total_tokens"] += r.total_tokens
        bucket["request_count"] += r.request_count
        bucket["cost_usd"] = round(bucket["cost_usd"] + (r.cost_usd or 0.0), 6)
    return out


def get_usage_summary(db: Session) -> dict:
    """Today's and this-month's cumulative token usage, in total and
    broken down by provider - the data behind GET /api/usage. Cheap:
    the table has at most a handful of rows per day."""
    today = _today_utc()
    month_prefix = today[:7]  # "YYYY-MM"

    today_rows = db.query(TokenUsageDaily).filter_by(date=today).all()
    month_rows = db.query(TokenUsageDaily).filter(TokenUsageDaily.date.like(f"{month_prefix}%")).all()

    return {
        "today": {"date": today, "total": _sum_rows(today_rows), "by_provider": _by_provider(today_rows)},
        "month": {"month": month_prefix, "total": _sum_rows(month_rows), "by_provider": _by_provider(month_rows)},
    }
