"""
Media-key simulation (Phase 6). Injects the same global media keys a
physical keyboard's play/pause/next/previous buttons send - whatever
app currently has the OS's media-key focus (Spotify, a browser tab,
Windows Media Player, ...) reacts to it, same as it would to a real
keyboard.

Windows-only (uses user32.keybd_event via ctypes - no new dependency).
Honest limitation, not hidden: the Windows API gives no feedback on
whether any application actually received/handled the key - only
whether the OS accepted the injected key event. So `success=True` means
"the key press was sent successfully," not "confirmed some app reacted
to it." There is no lightweight way to check for an active media
session without a much heavier dependency (the WinRT media-session
APIs), which isn't worth it for three media keys.
"""
from __future__ import annotations

import ctypes

from sqlalchemy.orm import Session

from app.tools.base import Tool, ToolResult, ToolSecurity

# Virtual-key codes for the standard multimedia keys (WinAPI winuser.h).
_VK_MEDIA_NEXT_TRACK = 0xB0
_VK_MEDIA_PREV_TRACK = 0xB1
_VK_MEDIA_PLAY_PAUSE = 0xB3

_ACTION_TO_VK = {
    "play_pause": _VK_MEDIA_PLAY_PAUSE,
    "next": _VK_MEDIA_NEXT_TRACK,
    "previous": _VK_MEDIA_PREV_TRACK,
}

_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002


class MediaControlTool(Tool):
    name = "media_control"
    description = (
        "Simulate a media key press (play/pause, next track, previous track) - affects "
        "whatever application currently has the OS media-key focus, same as a physical "
        "keyboard's media buttons would. Windows only. Cannot detect whether any app "
        "actually reacted, only whether the key press itself was sent."
    )
    parameters = {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["play_pause", "next", "previous"]}},
        "required": ["action"],
    }
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, action: str = "", **_) -> ToolResult:
        vk_code = _ACTION_TO_VK.get(action)
        if vk_code is None:
            return ToolResult(
                success=False,
                error=f"Unknown action '{action}'. Use 'play_pause', 'next', or 'previous'.",
            )

        if not hasattr(ctypes, "windll"):
            return ToolResult(success=False, error="Media control is only supported on Windows.")

        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            user32.keybd_event(vk_code, 0, _KEYEVENTF_EXTENDEDKEY, 0)
            user32.keybd_event(vk_code, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not send media key (blocked by the OS?): {exc}")
        except Exception as exc:  # noqa: BLE001 - a raw WinAPI call via ctypes, be defensive
            return ToolResult(success=False, error=f"Media key simulation failed unexpectedly: {exc}")

        return ToolResult(success=True, data={"action": action})
