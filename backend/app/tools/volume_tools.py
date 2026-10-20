"""
System volume (Windows Core Audio via pycaw, MIT).

COM objects are apartment-bound, so every call runs in a worker thread
that initializes COM for itself; nothing COM-related touches the event
loop thread.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict, Literal, Optional

from pydantic import Field, model_validator

from app.config import get_settings
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool


def _with_endpoint(fn):
    import comtypes  # noqa: PLC0415 - Windows-only
    from pycaw.pycaw import AudioUtilities  # noqa: PLC0415

    comtypes.CoInitialize()
    try:
        endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        return fn(endpoint)
    finally:
        comtypes.CoUninitialize()


def _read(endpoint) -> Dict[str, Any]:
    return {"level": round(endpoint.GetMasterVolumeLevelScalar() * 100), "muted": bool(endpoint.GetMute())}


def _apply(level: Optional[int], direction: Optional[str], step: int, mute: Optional[bool]):
    def op(endpoint) -> Dict[str, Any]:
        before = _read(endpoint)
        if mute is not None:
            endpoint.SetMute(1 if mute else 0, None)
        target = before["level"]
        if level is not None:
            target = level
        elif direction == "up":
            target = before["level"] + step
        elif direction == "down":
            target = before["level"] - step
        target = max(0, min(100, target))
        if level is not None or direction is not None:
            endpoint.SetMasterVolumeLevelScalar(target / 100.0, None)
            # Raising the volume of a muted device should make it audible.
            if direction == "up" or (level is not None and level > 0 and mute is None):
                endpoint.SetMute(0, None)
        after = _read(endpoint)
        return {"before": before, "after": after}

    return op


class SetVolumeArgs(ArgsModel):
    level: Optional[int] = Field(None, ge=0, le=100, description="Absolute Lautstärke in Prozent")
    direction: Optional[Literal["up", "down"]] = Field(None, description="up = lauter, down = leiser")
    step: Optional[int] = Field(None, ge=1, le=50, description="Schrittweite in Prozent für up/down")
    mute: Optional[bool] = Field(None, description="true = stumm, false = Ton wieder an")

    @model_validator(mode="after")
    def _need_something(self) -> "SetVolumeArgs":
        if self.level is None and self.direction is None and self.mute is None:
            raise ValueError("Gib level, direction oder mute an.")
        return self


@register_tool
class SetVolumeTool(Tool):
    name = "set_volume"
    description = (
        "Systemlautstärke ändern: absolut in Prozent (level), relativ lauter/leiser (direction up/down) "
        "oder stumm schalten (mute). Für 'mach die Musik leiser' ist das das richtige Tool, "
        "auch bei 'das ist zu laut' (direction down) oder 'ich hör nichts' (direction up)."
    )
    Args = SetVolumeArgs
    risk = RiskTier.CONTROL
    exposed_by_default = True

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32"

    async def execute(self, db=None, level: Optional[int] = None, direction: Optional[str] = None,
                      step: Optional[int] = None, mute: Optional[bool] = None, **_) -> ToolResult:
        step = step or get_settings().VOLUME_STEP_PERCENT
        try:
            result = await asyncio.to_thread(_with_endpoint, _apply(level, direction, step, mute))
        except ImportError:
            return ToolResult(success=False, error="Lautstärkesteuerung ist nur unter Windows verfügbar.")
        except OSError as exc:
            return ToolResult(success=False, error=f"Kein Audiogerät erreichbar: {exc}")
        return ToolResult(success=True, data=result)

    def describe(self, args: Dict[str, Any]) -> str:
        if args.get("mute") is True:
            return "Ton stumm schalten"
        if args.get("level") is not None:
            return f"Lautstärke auf {args['level']} % setzen"
        if args.get("direction"):
            return "Lauter" if args["direction"] == "up" else "Leiser"
        return "Ton wieder einschalten"


@register_tool
class GetVolumeTool(Tool):
    name = "get_volume"
    description = "Aktuelle Systemlautstärke in Prozent und ob der Ton stumm ist."
    risk = RiskTier.READ_ONLY
    exposed_by_default = True

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32"

    async def execute(self, db=None, **_) -> ToolResult:
        try:
            return ToolResult(success=True, data=await asyncio.to_thread(_with_endpoint, _read))
        except ImportError:
            return ToolResult(success=False, error="Lautstärkesteuerung ist nur unter Windows verfügbar.")
        except OSError as exc:
            return ToolResult(success=False, error=f"Kein Audiogerät erreichbar: {exc}")
