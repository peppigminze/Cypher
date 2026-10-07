"""Media control tests. They never touch the real OS: the WinRT session
call and the key press are replaced - an earlier version of these tests
pressed real media keys and skipped the user's music on every run."""
from __future__ import annotations

import ctypes

import pytest

from app.tools import media_tools
from app.tools.media_tools import MediaControlTool, NowPlayingTool


@pytest.fixture()
def fake_os(monkeypatch):
    calls = {"keys": [], "session": []}
    state = {"session": {"session": True, "app": "Spotify.exe", "status_before": "playing", "title": "Song", "artist": "Band"}}

    async def fake_session(action):
        calls["session"].append(action)
        info = state["session"]
        if info is None:
            return None
        return {**info, **({"accepted": True} if action else {})}

    monkeypatch.setattr(media_tools, "media_session", fake_session)
    monkeypatch.setattr(media_tools, "_press_media_key", lambda action: calls["keys"].append(action))
    monkeypatch.setattr(ctypes, "windll", object(), raising=False)
    return calls, state


async def test_pause_uses_the_media_session(fake_os):
    calls, _ = fake_os
    result = await MediaControlTool().execute(action="pause")
    assert result.success is True
    assert result.data["via"] == "media_session"
    assert calls["session"] == ["pause"] and calls["keys"] == []


async def test_falls_back_to_media_key_without_session_api(fake_os):
    calls, state = fake_os
    state["session"] = None
    result = await MediaControlTool().execute(action="next")
    assert result.success is True
    assert result.data["via"] == "media_key"
    assert calls["keys"] == ["next"]


async def test_pause_with_nothing_playing_is_a_noop(fake_os):
    calls, state = fake_os
    state["session"] = {"session": False}
    result = await MediaControlTool().execute(action="pause")
    assert result.success is True
    assert calls["keys"] == []


async def test_invalid_action_is_rejected(fake_os):
    result = await MediaControlTool().execute(action="shuffle")
    assert result.success is False
    assert "Unknown action" in result.error


def test_args_model_rejects_unknown_action():
    from app.tools.base import ToolArgumentError

    with pytest.raises(ToolArgumentError):
        MediaControlTool().validate_args({"action": "shuffle"})


async def test_unsupported_platform_fails_cleanly(monkeypatch):
    monkeypatch.delattr(ctypes, "windll", raising=False)
    result = await MediaControlTool().execute(action="next")
    assert result.success is False
    assert "Windows" in result.error


async def test_os_blocking_the_key_press_is_reported_cleanly(fake_os, monkeypatch):
    _, state = fake_os
    state["session"] = None

    def failing(action):
        raise OSError("access denied")

    monkeypatch.setattr(media_tools, "_press_media_key", failing)
    result = await MediaControlTool().execute(action="previous")
    assert result.success is False
    assert "access denied" in result.error


async def test_now_playing_reports_title(fake_os):
    result = await NowPlayingTool().execute()
    assert result.data["title"] == "Song" and result.data["playing"] is True
