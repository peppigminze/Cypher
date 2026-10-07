from __future__ import annotations

import ctypes

from app.tools.media_tools import MediaControlTool


async def test_media_control_play_pause_succeeds():
    result = await MediaControlTool().execute(action="play_pause")
    assert result.success is True
    assert result.data["action"] == "play_pause"


async def test_media_control_next_and_previous_succeed():
    for action in ("next", "previous"):
        result = await MediaControlTool().execute(action=action)
        assert result.success is True
        assert result.data["action"] == action


async def test_media_control_invalid_action_is_rejected():
    result = await MediaControlTool().execute(action="shuffle")
    assert result.success is False
    assert "Unknown action" in result.error


async def test_media_control_unsupported_platform_fails_cleanly(monkeypatch):
    # Simulate running on a non-Windows platform, where ctypes has no
    # `windll` attribute at all - must not crash, just report clearly.
    monkeypatch.delattr(ctypes, "windll", raising=False)
    result = await MediaControlTool().execute(action="next")
    assert result.success is False
    assert "Windows" in result.error


async def test_media_control_os_blocking_the_key_press_is_reported_cleanly(monkeypatch):
    class FailingUser32:
        def keybd_event(self, *_args):
            raise OSError("access denied")

    class FailingWindll:
        user32 = FailingUser32()

    monkeypatch.setattr(ctypes, "windll", FailingWindll(), raising=False)
    result = await MediaControlTool().execute(action="previous")
    assert result.success is False
    assert "access denied" in result.error
