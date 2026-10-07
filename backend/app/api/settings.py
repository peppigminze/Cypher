"""
Settings for the PWA Settings page (project spec section 30).

GET /api/settings is a read-only overview derived from server-side
configuration (.env) - there is no way for the PWA (or a compromised
client) to change security-relevant config like ALLOWED_DIRECTORIES or
tool confirmation policy over the network. To change those, edit .env
and restart the backend/agent.

The one deliberate exception (Phase 7): GET/POST /api/settings/provider
toggles which LLM provider the native tool-calling pipeline
(/api/tool-chat) uses - "local" or "cloud". This is NOT security-
relevant the way ALLOWED_DIRECTORIES is (it can't grant Cypher new
capabilities or bypass the approval gate, only change which model
answers), so a narrow, explicit write endpoint for it is fine. It is
never inferred or automatic - only ever changes via this endpoint,
persisted in SQLite (app/settings_store.py) so the choice survives a
restart instead of silently reverting to .env's static LLM_PROVIDER.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_user
from app.config import get_settings
from app.database.db import get_db
from app.schemas import SettingsOut, WakeOnLanInfo
from app.settings_store import VALID_PROVIDERS, get_active_provider, set_active_provider

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ActiveProviderOut(BaseModel):
    provider: str
    cloud_configured: bool  # whether CLOUD_LLM_API_KEY is set - lets the UI grey out "cloud" if switching would just fail


class SetActiveProviderIn(BaseModel):
    provider: str


@router.get("/provider", response_model=ActiveProviderOut)
def get_provider(db: Session = Depends(get_db), _user=Depends(require_user)) -> ActiveProviderOut:
    settings = get_settings()
    return ActiveProviderOut(
        provider=get_active_provider(db),
        cloud_configured=bool(settings.CLOUD_LLM_API_KEY),
    )


@router.post("/provider", response_model=ActiveProviderOut)
def set_provider(
    payload: SetActiveProviderIn, db: Session = Depends(get_db), _user=Depends(require_user)
) -> ActiveProviderOut:
    if payload.provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider '{payload.provider}'. Must be one of {VALID_PROVIDERS}.",
        )
    settings = get_settings()
    if payload.provider == "cloud" and not settings.CLOUD_LLM_API_KEY:
        # Fail the switch itself rather than silently accepting it and
        # only erroring on the next /api/tool-chat call - the user
        # should know immediately why "cloud" didn't take effect.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot switch to cloud - CLOUD_LLM_API_KEY is not set in .env.",
        )

    provider = set_active_provider(db, payload.provider)
    return ActiveProviderOut(provider=provider, cloud_configured=bool(settings.CLOUD_LLM_API_KEY))


@router.get("", response_model=SettingsOut)
def get_settings_overview(_user=Depends(require_user)) -> SettingsOut:
    settings = get_settings()

    wake_on_lan = WakeOnLanInfo(
        configured=bool(settings.WAKE_ON_LAN_MAC),
        mac_address=settings.WAKE_ON_LAN_MAC or None,
        broadcast=settings.WAKE_ON_LAN_BROADCAST,
        port=settings.WAKE_ON_LAN_PORT,
        command=(
            f"python wake_pc.py {settings.WAKE_ON_LAN_MAC} "
            f"--broadcast {settings.WAKE_ON_LAN_BROADCAST} --port {settings.WAKE_ON_LAN_PORT}"
            if settings.WAKE_ON_LAN_MAC
            else None
        ),
    )

    return SettingsOut(
        llm_provider=settings.LLM_PROVIDER,
        local_llm_model=settings.LOCAL_LLM_MODEL,
        local_llm_base_url=settings.LOCAL_LLM_BASE_URL,
        cloud_llm_enabled=settings.CLOUD_LLM_ENABLED,
        cloud_llm_model=settings.CLOUD_LLM_MODEL if settings.CLOUD_LLM_ENABLED else None,
        allowed_directories=settings.allowed_directories_list,
        message_max_retries=settings.MESSAGE_MAX_RETRIES,
        wake_on_lan=wake_on_lan,
    )
