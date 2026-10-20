"""
Power and display (FIX2 point 3).

    power           lock (control), sleep / shutdown / restart (critical),
                    cancel a pending shutdown (control)
    set_brightness  0-100 % - laptop display (WMI) or monitors with DDC/CI

Shutdown/restart go through `shutdown.exe /s|/r /t <delay>` with a default
delay of 30 s, so "Herunterfahren abbrechen" (shutdown /a) still works.
Tests replace the _lock/_sleep/_shutdown functions with dummies - these
actions are never really executed by the test suite.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import subprocess
import sys
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from app.pc import brightness
from app.security.classifier import CONTROL, CRITICAL, verdict
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

logger = logging.getLogger("cypher.tools.power")

DEFAULT_SHUTDOWN_DELAY = 30
SLEEP_DELAY = 3.0  # the spoken confirmation finishes before the PC sleeps
_pending: set = set()


def _windows_only(cls, settings: Any = None) -> bool:
    return sys.platform == "win32"


def _lock() -> None:
    if not ctypes.windll.user32.LockWorkStation():  # type: ignore[attr-defined]
        raise OSError("Sperren hat nicht geklappt.")


def _sleep() -> None:
    # SetSuspendState(hibernate=False, force=False, disable_wake_events=False)
    if not ctypes.WinDLL("powrprof").SetSuspendState(False, False, False):
        raise OSError("Energiesparmodus hat nicht geklappt (evtl. in Windows deaktiviert).")


async def _sleep_later() -> None:
    await asyncio.sleep(SLEEP_DELAY)
    try:
        await asyncio.to_thread(_sleep)
    except OSError as exc:
        logger.warning("Sleep failed: %s", exc)


def _shutdown(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["shutdown.exe", *args], capture_output=True, timeout=15, creationflags=0x08000000)


class PowerArgs(ArgsModel):
    action: Literal["lock", "sleep", "shutdown", "restart", "cancel"] = Field(..., description="cancel = geplantes Herunterfahren abbrechen")
    delay: Optional[int] = Field(None, ge=0, le=3600, description="Sekunden bis shutdown/restart, Standard 30")


@register_tool
class PowerTool(Tool):
    name = "power"
    description = "PC sperren, in den Energiesparmodus, herunterfahren, neu starten oder geplantes Herunterfahren abbrechen."
    Args = PowerArgs
    risk = RiskTier.CRITICAL
    confirm_writes = False
    exposed_by_default = True

    is_available = classmethod(_windows_only)

    def classify(self, args: Dict[str, Any], ctx=None):
        action = args.get("action")
        if action in ("lock", "cancel"):
            return verdict(CONTROL)
        if action == "sleep":
            return verdict(CRITICAL, "Energiesparmodus: laufende Programme und Downloads werden angehalten", "power")
        return verdict(CRITICAL, "Herunterfahren/Neustart: ungespeicherte Arbeit geht verloren", "shutdown")

    def describe(self, args: Dict[str, Any]) -> str:
        labels = {"lock": "PC sperren", "sleep": "Energiesparmodus", "shutdown": "PC herunterfahren", "restart": "PC neu starten", "cancel": "Herunterfahren abbrechen"}
        text = labels.get(args.get("action", ""), "Energie")
        if args.get("action") in ("shutdown", "restart"):
            text += f" in {args.get('delay') if args.get('delay') is not None else DEFAULT_SHUTDOWN_DELAY} s"
        return text

    async def execute(self, db=None, action: str = "", delay: Optional[int] = None, **_) -> ToolResult:
        try:
            if action == "lock":
                await asyncio.to_thread(_lock)
                return ToolResult(success=True, data={"locked": True})
            if action == "sleep":
                task = asyncio.create_task(_sleep_later())
                _pending.add(task)
                task.add_done_callback(_pending.discard)
                return ToolResult(success=True, data={"sleep_in_seconds": SLEEP_DELAY})
            if action in ("shutdown", "restart"):
                seconds = DEFAULT_SHUTDOWN_DELAY if delay is None else delay
                proc = await asyncio.to_thread(_shutdown, ["/s" if action == "shutdown" else "/r", "/t", str(seconds)])
                if proc.returncode != 0:
                    return ToolResult(success=False, error=_text(proc) or f"shutdown.exe Exit-Code {proc.returncode}")
                return ToolResult(success=True, data={"action": action, "in_seconds": seconds, "cancel_hint": "Abbrechen: power action=cancel"})
            if action == "cancel":
                proc = await asyncio.to_thread(_shutdown, ["/a"])
                if proc.returncode != 0:
                    return ToolResult(success=False, error="Es war kein Herunterfahren geplant.")
                return ToolResult(success=True, data={"cancelled": True})
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=False, error=f"Unbekannte Aktion '{action}'.")


def _text(proc: subprocess.CompletedProcess) -> str:
    raw = proc.stderr or proc.stdout or b""
    for encoding in ("utf-8", "cp850"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return ""


class BrightnessArgs(ArgsModel):
    level: int = Field(..., ge=0, le=100, description="Helligkeit in Prozent")


@register_tool
class SetBrightnessTool(Tool):
    name = "set_brightness"
    description = "Bildschirmhelligkeit in Prozent einstellen."
    Args = BrightnessArgs
    risk = RiskTier.CONTROL
    confirm_writes = False
    exposed_by_default = True

    is_available = classmethod(_windows_only)

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Helligkeit auf {args.get('level')} %"

    async def execute(self, db=None, level: int = 50, **_) -> ToolResult:
        try:
            method = await asyncio.to_thread(brightness.set_brightness, level)
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"level": level, "method": method})
