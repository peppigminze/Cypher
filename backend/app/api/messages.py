"""
/api/messages - the old chat endpoint, kept working for existing clients
(e.g. a phone shortcut) after the separate PC-agent process was removed (M5).

Before: POST stored the message as "pending" and a second process
(agent/run_agent.py) picked it up minutes or seconds later. Now the backend
runs on the PC itself, so POST answers directly through the conversation
engine (same agent as voice and /api/chat) and returns the finished
message. The table and its history stay as they were - no data migration.

New clients should use /api/chat (streamed, with approvals in the chat).
If a turn needs an approval, the response says so; it can be answered in
the app (approval list) or via POST /api/chat/approval.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.engine import ApprovalRequired, ConversationEngine, ToolFinished, TurnDone, TurnFailed, conversations
from app.auth import require_user
from app.config import get_settings
from app.core import errors
from app.database.db import get_db
from app.database.models import Message, MessageStatus
from app.llm.base import LLMUnavailableError
from app.llm.factory import get_provider_by_name
from app.schemas import MessageCreate, MessageOut
from app.security.rate_limit import rate_limit
from app.settings_store import get_active_provider
from app.tools.catalog import build_registry

logger = logging.getLogger("cypher.api.messages")
router = APIRouter(prefix="/api/messages", tags=["messages"])

# All /api/messages turns share one conversation, like the old pipeline's single chat.
LEGACY_CONVERSATION_ID = "0000legacy000000"


async def _answer(db: Session, message: Message) -> None:
    provider_name = get_active_provider(db)
    message.status = MessageStatus.processing
    message.processed_by = provider_name
    db.commit()
    try:
        engine = ConversationEngine(get_provider_by_name(provider_name), build_registry(), provider_name=provider_name, settings=get_settings(), store=conversations)
        observations: list = []
        async for event in engine.run_turn(LEGACY_CONVERSATION_ID, message.content, source="text", db=db):
            if isinstance(event, ToolFinished):
                observations.append({"tool": event.name, "arguments": event.arguments, "success": event.success, "error": event.error})
            elif isinstance(event, ApprovalRequired):
                message.response = f"{event.spoken_prompt} (Bitte in der App bestätigen.)"
                message.status = MessageStatus.completed
            elif isinstance(event, TurnDone):
                message.response = event.reply
                message.status = MessageStatus.completed
            elif isinstance(event, TurnFailed):
                message.error = event.error.message
                message.status = MessageStatus.failed
        message.observations = observations
    except LLMUnavailableError as exc:
        message.error = errors.from_exception(exc).message
        message.status = MessageStatus.failed
    except Exception:  # noqa: BLE001
        logger.exception("Legacy message turn crashed")
        message.error = errors.internal_error().message
        message.status = MessageStatus.failed
    message.processed_at = datetime.now(timezone.utc)
    db.commit()


@router.post("", response_model=MessageOut, dependencies=[Depends(rate_limit("chat"))])
async def create_message(payload: MessageCreate, db: Session = Depends(get_db), _user=Depends(require_user)) -> Message:
    if payload.client_id:
        existing = db.query(Message).filter(Message.client_id == payload.client_id).first()
        if existing:
            return existing
    message = Message(content=payload.content, client_id=payload.client_id)
    db.add(message)
    db.commit()
    await _answer(db, message)
    db.refresh(message)
    return message


@router.get("", response_model=List[MessageOut])
def list_messages(db: Session = Depends(get_db), _user=Depends(require_user), limit: int = 50) -> list[Message]:
    stmt = select(Message).order_by(Message.created_at.desc()).limit(limit)
    messages = list(db.execute(stmt).scalars().all())
    messages.reverse()
    return messages
