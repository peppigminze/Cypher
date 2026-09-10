"""
In-memory pending-approval store for the native tool-calling pipeline
(app/agent/native_tool_agent.py, app/api/tool_chat.py).

When the model wants to call a tool whose ToolSecurity is not SAFE (see
app/tools/base.py - currently just write_file), the agent pauses instead
of executing it and stores everything needed to resume here: the
in-progress message history, the tool-call log so far, and any further
tool_calls from the same model turn that still need processing once the
pending one is resolved. The client gets back an `approval_id` and must
call POST /api/tool-chat/confirm to approve or reject before anything
happens.

In-memory and per-process by design - this mirrors the rest of the
tool-calling pipeline's scope (a single local backend process, not a
distributed deployment). Entries expire after PENDING_TTL_SECONDS so an
abandoned confirmation can't accumulate forever; there is deliberately
no persistence across a backend restart, same as the existing SQLite-
backed PendingAction model in the OLD pipeline does persist (that one
is untouched by this - see the module docstring in native_tool_agent.py
for why the two pipelines don't share this piece of state).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PENDING_TTL_SECONDS = 5 * 60


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
    created_at: float = field(default_factory=time.monotonic)


_store: Dict[str, PendingApproval] = {}


def _evict_expired() -> None:
    now = time.monotonic()
    expired = [pid for pid, p in _store.items() if now - p.created_at > PENDING_TTL_SECONDS]
    for pid in expired:
        _store.pop(pid, None)


def create(
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    tool_call_id: str,
    messages: List[Dict[str, Any]],
    call_log: List[Any],
    steps_used: int,
    remaining_calls: Optional[List[Dict[str, Any]]] = None,
) -> str:
    _evict_expired()
    approval_id = uuid.uuid4().hex
    _store[approval_id] = PendingApproval(
        approval_id=approval_id,
        tool_name=tool_name,
        arguments=arguments,
        tool_call_id=tool_call_id,
        messages=messages,
        call_log=call_log,
        steps_used=steps_used,
        remaining_calls=remaining_calls or [],
    )
    return approval_id


def pop(approval_id: str) -> Optional[PendingApproval]:
    """Fetch and remove an entry - single-use, so confirming twice (e.g.
    a double-click) can't run the tool twice."""
    _evict_expired()
    return _store.pop(approval_id, None)
