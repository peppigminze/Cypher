"""
When does a tool call need the user's approval? (FIX2 point 3c/3d)

Input: the deterministic Verdict (app/security/classifier.py or the tool's
own tier) - never the model's opinion.

    tainted turn + write or more   -> ask (3d: untrusted content can't act alone)
    critical + red-line category   -> ask, ALWAYS (even with the prompt off)
    critical                       -> ask while "Sicherheitsabfrage" is on (default)
    write                          -> ask only for tools that always did
                                      (notes, forgetting memories); PC file
                                      operations in your profile run directly
    safe / control                 -> run

The switch itself ("Sicherheitsabfrage für kritische Aktionen") lives here.
It can be turned off for this session (back on after a restart) or
permanently, and ONLY through the local UI (app/api/security_api.py checks
that) - no tool, no voice command and no tool result can change it. Every
change is written to the audit log.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, Literal, Optional

from app.config import get_settings
from app.tools.base import RiskTier

logger = logging.getLogger("cypher.security.policy")

CONFIRM_KEY = "security.critical_confirm"
Scope = Literal["session", "permanent"]

RED_LINE_LABELS = {
    "format": "Formatieren / Partitionieren",
    "system_folders": "Löschen von Systemordnern",
    "boot": "Boot-Konfiguration",
    "defender_firewall": "Defender oder Firewall abschalten/ändern",
    "cypher_secrets": "Cyphers Token, Konfiguration oder Audit-Log",
}

_lock = threading.Lock()
_session_off = False
_cache: Dict[str, Any] = {"at": 0.0, "permanent_on": True}


def red_line(settings=None) -> frozenset:
    settings = settings or get_settings()
    return frozenset(c.strip() for c in settings.CRITICAL_RED_LINE.split(",") if c.strip())


def _permanent_on() -> bool:
    now = time.monotonic()
    with _lock:
        if now - _cache["at"] < 2.0:
            return _cache["permanent_on"]
    from app.database.db import session_scope  # noqa: PLC0415
    from app.database.models import RuntimeSetting  # noqa: PLC0415

    try:
        with session_scope() as db:
            row = db.get(RuntimeSetting, CONFIRM_KEY)
            value = row is None or row.value != "off"
    except Exception:  # noqa: BLE001 - when in doubt: ask
        logger.exception("Could not read the safety prompt setting - asking")
        value = True
    with _lock:
        _cache.update(at=now, permanent_on=value)
    return value


def confirm_state() -> Dict[str, Any]:
    permanent = _permanent_on()
    enabled = permanent and not _session_off
    mode = "on" if enabled else ("off_permanent" if not permanent else "off_session")
    lines = red_line()
    return {
        "enabled": enabled,
        "mode": mode,
        "red_line": sorted(lines),
        "red_line_labels": [RED_LINE_LABELS.get(c, c) for c in sorted(lines)],
    }


def critical_confirm_enabled() -> bool:
    return confirm_state()["enabled"]


async def set_critical_confirm(enabled: bool, scope: Scope, *, actor: str = "ui") -> Dict[str, Any]:
    """Only called by the local-UI endpoint (security_api.py)."""
    global _session_off
    from app.database.db import session_scope  # noqa: PLC0415
    from app.database.models import RuntimeSetting  # noqa: PLC0415
    from app.tools.audit import AuditRecord, get_audit_log  # noqa: PLC0415

    before = confirm_state()["mode"]
    with _lock:
        if enabled:
            _session_off = False
        elif scope == "session":
            _session_off = True
    if enabled or scope == "permanent":
        with session_scope() as db:
            row = db.get(RuntimeSetting, CONFIRM_KEY)
            value = "on" if enabled else "off"
            if row is None:
                db.add(RuntimeSetting(key=CONFIRM_KEY, value=value))
            else:
                row.value = value
    with _lock:
        _cache["at"] = 0.0
    state = confirm_state()
    text = {"on": "Sicherheitsabfrage AN", "off_session": "Sicherheitsabfrage AUS (nur diese Sitzung)", "off_permanent": "Sicherheitsabfrage AUS (dauerhaft)"}
    await get_audit_log().write(AuditRecord(tool="setting.critical_confirm", decision="setting_changed", risk="critical",
                                            arguments={"enabled": enabled, "scope": scope, "before": before, "after": state["mode"]},
                                            summary=text[state["mode"]], success=True, source=actor))
    logger.warning("Safety prompt for critical actions: %s -> %s", before, state["mode"])
    return state


def reset_session_state() -> None:
    """Tests: forget a session-only switch and the cache."""
    global _session_off
    with _lock:
        _session_off = False
        _cache["at"] = 0.0


def approval_reason(level: RiskTier, categories: Iterable[str], *, tainted: bool, confirm_writes: bool) -> Optional[str]:
    """Why this call needs the user's approval - or None if it may run."""
    cats = set(categories)
    if level == RiskTier.BLOCKED:
        return None  # never runs at all (executor)
    if tainted and level.rank >= RiskTier.WRITE.rank:
        return "untrusted"
    if level == RiskTier.CRITICAL:
        if cats & red_line():
            return "red_line"
        if critical_confirm_enabled():
            return "critical"
        return None
    if level == RiskTier.WRITE and confirm_writes:
        return "write"
    return None


REASON_TEXT = {
    "untrusted": "Der Auftrag kommt (auch) aus fremdem Inhalt (Webseite, Datei, Zwischenablage).",
    "red_line": "Rote Linie: fragt immer, auch wenn die Sicherheitsabfrage aus ist.",
    "critical": "Kritische Aktion.",
    "write": "Ändert deine Daten.",
}
