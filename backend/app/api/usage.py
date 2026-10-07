"""
GET /api/usage - cumulative token usage for the native tool-calling
pipeline (Phase 7 cost control, see app/settings_store.py). No UI is
required for this yet (per the Phase 7 spec) - a plain JSON endpoint is
enough to check cumulative spend beyond what's scattered across console
logs (see the TOKEN USAGE log lines in app/llm/local_provider.py and
app/llm/cloud_provider.py).
"""
from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database.db import get_db
from app.settings_store import get_usage_summary

router = APIRouter(tags=["usage"])


class UsageTotals(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    request_count: int


class UsagePeriod(BaseModel):
    total: UsageTotals
    by_provider: Dict[str, UsageTotals]


class UsageOut(BaseModel):
    today: UsagePeriod
    month: UsagePeriod


@router.get("/api/usage", response_model=UsageOut)
def get_usage(db: Session = Depends(get_db), _user=Depends(require_user)) -> UsageOut:
    summary = get_usage_summary(db)
    return UsageOut(
        today=UsagePeriod(
            total=UsageTotals(**summary["today"]["total"]),
            by_provider={k: UsageTotals(**v) for k, v in summary["today"]["by_provider"].items()},
        ),
        month=UsagePeriod(
            total=UsageTotals(**summary["month"]["total"]),
            by_provider={k: UsageTotals(**v) for k, v in summary["month"]["by_provider"].items()},
        ),
    )
