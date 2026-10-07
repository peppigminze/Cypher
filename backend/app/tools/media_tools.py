"""
Media control via Windows' system media session (the same API the
volume flyout / lock screen uses), with a media-key fallback.

Why the session API: the old tool could only press the play/pause *key*,
which toggles - "Pausier die Musik" would start music that was already
paused. The session API knows the playback state, can pause/play
explicitly and tell what's playing ("Was läuft gerade?").

WinRT calls run in a worker thread with their own event loop so COM
apartment state never touches the server's event loop thread.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

logger = logging.getLogger("cypher.tools.media")

_VK = {"next": 0xB0, "previous": 0xB1, "stop": 0xB2, "play_pause": 0xB3}
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002

# GlobalSystemMediaTransportControlsSessionPlaybackStatus
_STATUS_NAMES = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}

MediaAction = Literal["play", "pause", "play_pause", "next", "previous", "stop"]


def _press_media_key(action: str) -> None:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    vk = _VK["play_pause" if action in ("play", "pause") else action]
    user32.keybd_event(vk, 0, _KEYEVENTF_EXTENDEDKEY, 0)
    user32.keybd_event(vk, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)


async def _session_op(action: Optional[str]) -> Dict[str, Any]:
    from winrt.windows.media.control import (  # noqa: PLC0415 - Windows-only import
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )

    manager = await Manager.request_async()
    session = manager.get_current_session()
    if session is None:
        return {"session": False}
    status = _STATUS_NAMES.get(int(session.get_playback_info().playback_status), "unknown")
    info: Dict[str, Any] = {"session": True, "app": session.source_app_user_model_id, "status_before": status}
    if action is not None:
        if action == "play":
            ok = True if status == "playing" else await session.try_play_async()
        elif action == "pause":
            ok = True if status in ("paused", "stopped") else await session.try_pause_async()
        elif action == "play_pause":
            ok = await session.try_toggle_play_pause_async()
        elif action == "next":
            ok = await session.try_skip_next_async()
        elif action == "previous":
            ok = await session.try_skip_previous_async()
        else:
            ok = await session.try_stop_async()
        info["accepted"] = bool(ok)
    try:
        props = await session.try_get_media_properties_async()
        info["title"] = props.title or None
        info["artist"] = props.artist or None
    except OSError:
        pass
    return info


def _run_session_op(action: Optional[str]) -> Dict[str, Any]:
    return asyncio.run(_session_op(action))


async def media_session(action: Optional[str]) -> Optional[Dict[str, Any]]:
    """Run a session operation; None if the WinRT API is unavailable."""
    if sys.platform != "win32":
        return None
    try:
        return await asyncio.to_thread(_run_session_op, action)
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001 - WinRT/COM errors vary wildly
        logger.warning("Media session API failed (%s) - falling back to media keys", exc)
        return None


class MediaControlArgs(ArgsModel):
    action: MediaAction = Field(
        ...,
        description="play = fortsetzen, pause = anhalten, play_pause = umschalten, next = nächster Titel, previous = vorheriger Titel, stop",
    )


@register_tool
class MediaControlTool(Tool):
    name = "media_control"
    description = (
        "Musik/Medienwiedergabe steuern (Spotify, Browser usw.): pausieren, fortsetzen, nächster oder vorheriger Titel. "
        "Nicht für die Lautstärke - dafür set_volume."
    )
    Args = MediaControlArgs
    risk = RiskTier.CONTROL
    exposed_by_default = True

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32"

    async def execute(self, db=None, action: str = "", **_) -> ToolResult:
        if action not in _VK and action not in ("play", "pause"):
            return ToolResult(success=False, error=f"Unknown action '{action}'. Use play, pause, play_pause, next, previous or stop.")
        if not hasattr(ctypes, "windll"):
            return ToolResult(success=False, error="Media control is only supported on Windows.")
        info = await media_session(action)
        if info is not None and info.get("session"):
            return ToolResult(success=bool(info.get("accepted", True)), data={"action": action, "via": "media_session", **info},
                              error=None if info.get("accepted", True) else "Die Wiedergabe-App hat den Befehl abgelehnt.")
        if info is not None and not info.get("session"):
            if action in ("pause", "stop"):
                return ToolResult(success=True, data={"action": action, "note": "Es läuft gerade keine Wiedergabe."})
        try:
            _press_media_key(action)
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not send media key: {exc}")
        return ToolResult(success=True, data={"action": action, "via": "media_key", "note": "Medientaste gesendet (ohne Rückmeldung der App)."})

    def describe(self, args: Dict[str, Any]) -> str:
        return {"play": "Wiedergabe fortsetzen", "pause": "Wiedergabe pausieren", "play_pause": "Wiedergabe umschalten",
                "next": "Nächster Titel", "previous": "Vorheriger Titel", "stop": "Wiedergabe stoppen"}.get(args.get("action", ""), "Medien steuern")


@register_tool
class NowPlayingTool(Tool):
    name = "get_now_playing"
    description = "Abfragen, welcher Titel gerade läuft (Titel, Interpret, App, ob er läuft oder pausiert ist)."
    risk = RiskTier.READ_ONLY
    exposed_by_default = True

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32"

    async def execute(self, db=None, **_) -> ToolResult:
        info = await media_session(None)
        if info is None:
            return ToolResult(success=False, error="Die Windows-Medienschnittstelle ist nicht verfügbar.")
        if not info.get("session"):
            return ToolResult(success=True, data={"playing": False, "note": "Es läuft gerade keine Wiedergabe."})
        return ToolResult(success=True, data={"playing": info.get("status_before") == "playing", "status": info.get("status_before"),
                                              "title": info.get("title"), "artist": info.get("artist"), "app": info.get("app")})
