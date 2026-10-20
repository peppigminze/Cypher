"""
Pending approvals for write/destructive tool calls.

When the model proposes a call that needs approval, the agent pauses and
stores everything needed to resume here; the UI (or a spoken "ja"/"nein")
resolves it via /api/tool-chat/confirm or the voice session.

Kept in memory on purpose: the paused state includes the running
conversation, and a pending approval must not outlive the context in
which the user saw it (after a restart the user simply asks again). Every
lifecycle step (requested / approved / rejected / expired) is written to
the persistent audit log (app/tools/audit.py), so nothing is lost from
the record.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.core.events import bus

# Tests may override this; None = APPROVAL_TTL_SECONDS from config.
PENDING_TTL_SECONDS: Optional[int] = None


def ttl_seconds() -> int:
    return PENDING_TTL_SECONDS if PENDING_TTL_SECONDS is not None else get_settings().APPROVAL_TTL_SECONDS


@dataclass
class PendingApproval:
    approval_id: str
    tool_name: str
    arguments: Dict[str, Any]
    tool_call_id: str
    messages: List[Dict[str, Any]]
    call_log: List[Any]
    steps_used: int
    remaining_calls: List[Dict[str, Any]] = field(default_factory=list)
    risk: str = "write"
    summary: str = ""
    provider: str = "local"
    source: str = "text"
    conversation_id: Optional[str] = None
    user_message: str = ""
    tainted: bool = False
    reason: str = ""  # classifier reason (shown on the card)
    kind: str = ""  # policy: "critical" | "red_line" | "untrusted" | "write"
    created_at: float = field(default_factory=time.monotonic)
    created_wall: float = field(default_factory=time.time)

    @property
    def expires_in(self) -> float:
        return max(0.0, ttl_seconds() - (time.monotonic() - self.created_at))

    def public(self) -> Dict[str, Any]:
        from app.security.policy import needs_voice_phrase  # noqa: PLC0415

        return {
            "approval_id": self.approval_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "risk": self.risk,
            "summary": self.summary,
            "reason": self.reason,
            "kind": self.kind,
            "voice_phrase": "Ja, ausführen" if needs_voice_phrase(self.risk) else "Ja",
            "expires_in_seconds": int(self.expires_in),
            "created_at": self.created_wall,
            "conversation_id": self.conversation_id,
        }


_store: Dict[str, PendingApproval] = {}
_lock = threading.Lock()
# Expired approvals not yet reported to the audit log.
_expired_unreported: List[PendingApproval] = []


def _evict_expired() -> None:
    ttl = ttl_seconds()
    now = time.monotonic()
    for pid, p in list(_store.items()):
        if now - p.created_at > ttl:
            _store.pop(pid, None)
            _expired_unreported.append(p)
            bus.publish("approval.expired", approval_id=pid, tool=p.tool_name)


def create(
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    tool_call_id: str,
    messages: List[Dict[str, Any]],
    call_log: List[Any],
    steps_used: int,
    remaining_calls: Optional[List[Dict[str, Any]]] = None,
    risk: str = "write",
    summary: str = "",
    provider: str = "local",
    source: str = "text",
    conversation_id: Optional[str] = None,
    user_message: str = "",
    tainted: bool = False,
    reason: str = "",
    kind: str = "",
) -> str:
    approval_id = uuid.uuid4().hex
    entry = PendingApproval(
        approval_id=approval_id,
        tool_name=tool_name,
        arguments=arguments,
        tool_call_id=tool_call_id,
        messages=messages,
        call_log=call_log,
        steps_used=steps_used,
        remaining_calls=remaining_calls or [],
        risk=risk,
        summary=summary,
        provider=provider,
        source=source,
        conversation_id=conversation_id,
        user_message=user_message,
        tainted=tainted,
        reason=reason,
        kind=kind,
    )
    with _lock:
        _evict_expired()
        _store[approval_id] = entry
    bus.publish("approval.created", **entry.public())
    return approval_id


def pop(approval_id: str) -> Optional[PendingApproval]:
    """Fetch and remove an entry - single use, so a double-click on "Ja"
    can never run the tool twice."""
    with _lock:
        _evict_expired()
        entry = _store.pop(approval_id, None)
    if entry is not None:
        bus.publish("approval.resolved", approval_id=approval_id, tool=entry.tool_name)
    return entry


def peek(approval_id: str) -> Optional[PendingApproval]:
    with _lock:
        _evict_expired()
        return _store.get(approval_id)


def list_pending() -> List[PendingApproval]:
    with _lock:
        _evict_expired()
        return sorted(_store.values(), key=lambda p: p.created_at)


def take_expired_unreported() -> List[PendingApproval]:
    with _lock:
        items = list(_expired_unreported)
        _expired_unreported.clear()
        return items


def clear() -> None:
    with _lock:
        _store.clear()
        _expired_unreported.clear()
