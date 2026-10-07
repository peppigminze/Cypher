"""New M2 tools. Nothing here touches the real OS (volume, clipboard, app
launches, screen and the network are all faked)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app import scheduler
from app.config import get_settings
from app.core.events import bus
from app.database.db import Base, engine
from app.tools import app_tools, clipboard_tools, screen_tools, volume_tools, weather_tools
from app.tools.app_tools import OpenAppTool, _match_app
from app.tools.base import RiskTier
from app.tools.clipboard_tools import ReadClipboardTool, WriteClipboardTool
from app.tools.context import ToolContext
from app.tools.note_tools import ListNotesTool, SaveNoteTool
from app.tools.screen_tools import DescribeScreenTool
from app.tools.timer_tools import CancelTimerTool, ListTimersTool, SetTimerTool, _spoken_duration
from app.tools.volume_tools import GetVolumeTool, SetVolumeTool
from app.tools.weather_tools import GetWeatherTool


@pytest.fixture()
def db_tables():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


# ------------------------------------------------------------------ timers
async def test_timer_create_list_cancel(db_tables):
    created = await SetTimerTool().execute(seconds=600, label="Wäsche")
    assert created.success and created.data["duration_text"] == "10 Minuten"
    listed = await ListTimersTool().execute()
    assert listed.data["count"] == 1 and listed.data["timers"][0]["label"] == "Wäsche"
    cancelled = await CancelTimerTool().execute(label="wäsche")
    assert cancelled.success
    assert (await ListTimersTool().execute()).data["count"] == 0


async def test_due_timer_fires_once_and_is_announced(db_tables):
    received = []

    async def listen():
        async for event in bus.subscribe():
            received.append(event)
            return

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0)
    await SetTimerTool().execute(seconds=1, label="Tee")
    await asyncio.sleep(1.1)
    fired = await asyncio.to_thread(scheduler.fire_due_reminders)
    assert [f["title"] for f in fired] == ["Tee"] and fired[0]["is_timer"]
    await asyncio.wait_for(listener, timeout=2)
    assert received[0].type == "reminder.due" and received[0].data["title"] == "Tee"
    assert await asyncio.to_thread(scheduler.fire_due_reminders) == []  # never twice


def test_spoken_duration():
    assert _spoken_duration(90) == "1 Minute und 30 Sekunden"
    assert _spoken_duration(3600) == "1 Stunde"
    assert _spoken_duration(5400) == "1 Stunde und 30 Minuten"


# ------------------------------------------------------------------ notes
async def test_save_and_list_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTES_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        tool = SaveNoteTool()
        assert tool.base_risk() == RiskTier.WRITE
        assert "Milch" in tool.describe({"text": "Milch und Brot kaufen"})
        result = await tool.execute(text="Milch und Brot kaufen", title="Einkauf")
        assert result.success
        files = list(tmp_path.glob("*.md"))
        assert len(files) == 1 and "Milch und Brot kaufen" in files[0].read_text(encoding="utf-8")
        notes = (await ListNotesTool().execute()).data["notes"]
        assert notes[0]["text"] == "Milch und Brot kaufen" and "Einkauf" in notes[0]["heading"]
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------------------ volume
class FakeEndpoint:
    def __init__(self, level=0.4, muted=0):
        self.level, self.muted = level, muted

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, _ctx):
        self.level = v

    def GetMute(self):
        return self.muted

    def SetMute(self, m, _ctx):
        self.muted = m


@pytest.fixture()
def fake_endpoint(monkeypatch):
    ep = FakeEndpoint()
    monkeypatch.setattr(volume_tools, "_with_endpoint", lambda fn: fn(ep))
    return ep


async def test_volume_down_up_absolute_mute(fake_endpoint):
    tool = SetVolumeTool()
    assert (await tool.execute(direction="down")).data["after"]["level"] == 30
    assert (await tool.execute(direction="up", step=25)).data["after"]["level"] == 55
    assert (await tool.execute(level=100)).data["after"]["level"] == 100
    result = await tool.execute(mute=True)
    assert result.data["after"]["muted"] is True
    assert (await GetVolumeTool().execute()).data == {"level": 100, "muted": True}


def test_volume_args_need_something():
    from app.tools.base import ToolArgumentError

    with pytest.raises(ToolArgumentError):
        SetVolumeTool().validate_args({})
    with pytest.raises(ToolArgumentError):
        SetVolumeTool().validate_args({"level": 101})


# ------------------------------------------------------------------ clipboard
async def test_clipboard_roundtrip_with_fake_os(monkeypatch):
    store = {"text": None}
    monkeypatch.setattr(clipboard_tools, "write_clipboard_text", lambda t: store.update(text=t))
    monkeypatch.setattr(clipboard_tools, "read_clipboard_text", lambda: store["text"])
    assert (await ReadClipboardTool().execute()).data["empty"] is True
    assert (await WriteClipboardTool().execute(text="Hallo Welt")).success
    assert (await ReadClipboardTool().execute()).data["text"] == "Hallo Welt"


def test_clipboard_read_needs_approval_only_in_cloud_mode():
    tool = ReadClipboardTool()
    assert tool.risk_for({}, ToolContext(provider="local")) == RiskTier.READ_ONLY
    assert tool.risk_for({}, ToolContext(provider="cloud")) == RiskTier.WRITE
    assert WriteClipboardTool.allow_when_tainted is False


# ------------------------------------------------------------------ open_app
def test_app_name_matching():
    whitelist = {"rechner": "calc.exe", "taschenrechner": "calc.exe", "spotify": "spotify:", "editor": "notepad.exe"}
    assert _match_app("Spotify", whitelist) == "spotify"
    assert _match_app("den Taschenrechner", whitelist) == "taschenrechner"
    assert _match_app("Word", whitelist) is None
    assert _match_app("powershell -c rm", whitelist) is None


async def test_open_app_only_launches_whitelisted_targets(monkeypatch):
    launched = []
    monkeypatch.setattr(app_tools.os, "startfile", lambda target: launched.append(target), raising=False)
    ok = await OpenAppTool().execute(name="Spotify")
    assert ok.success and launched == ["spotify:"]
    refused = await OpenAppTool().execute(name="cmd /c del *")
    assert refused.success is False and "nicht freigegeben" in refused.error
    assert launched == ["spotify:"]


# ------------------------------------------------------------------ weather
async def test_weather_uses_open_meteo(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding" in request.url.host:
            return httpx.Response(200, json={"results": [{"name": "Zürich", "latitude": 47.37, "longitude": 8.55, "country_code": "CH", "admin1": "Zürich"}]})
        assert request.url.params["forecast_days"] == "2"
        return httpx.Response(200, json={
            "current": {"temperature_2m": 16.4, "apparent_temperature": 16.8, "weather_code": 3, "wind_speed_10m": 2.6},
            "daily": {"weather_code": [61, 80], "temperature_2m_max": [21.9, 16.2], "temperature_2m_min": [12.7, 12.0], "precipitation_probability_max": [25, 100]},
        })

    monkeypatch.setattr(weather_tools, "_transport", httpx.MockTransport(handler))
    today = await GetWeatherTool().execute(location="Zürich")
    assert today.data["now"]["condition"] == "bedeckt" and today.data["forecast"]["temp_max_c"] == 21.9
    tomorrow = await GetWeatherTool().execute(location="Zürich", day="tomorrow")
    assert tomorrow.data["forecast"]["condition"] == "leichte Regenschauer" and "now" not in tomorrow.data


async def test_weather_without_location_asks_back():
    result = await GetWeatherTool().execute()
    assert result.success is False and "Ort" in result.error


# ------------------------------------------------------------------ screen
async def test_describe_screen_uses_local_vision_model(monkeypatch):
    from app.llm.base import StreamDone, TextDelta

    class FakeProvider:
        def __init__(self):
            self.messages = None

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.messages = messages
            yield TextDelta("Ein Code-Editor mit Python-Datei.")
            yield StreamDone()

    provider = FakeProvider()

    async def has_vision(settings):
        return True

    monkeypatch.setattr(screen_tools, "model_has_vision", has_vision)
    monkeypatch.setattr(screen_tools, "capture_screenshot_jpeg_b64", lambda: "aGVsbG8=")
    monkeypatch.setattr(screen_tools, "build_local_provider", lambda settings: provider)
    tool = DescribeScreenTool()
    result = await tool.execute()
    assert result.data["description"] == "Ein Code-Editor mit Python-Datei."
    assert provider.messages[0]["images"] == ["aGVsbG8="]
    assert tool.untrusted_output is True
    assert tool.risk_for({}, ToolContext(provider="cloud")) == RiskTier.WRITE


async def test_describe_screen_refuses_without_vision(monkeypatch):
    async def no_vision(settings):
        return False

    monkeypatch.setattr(screen_tools, "model_has_vision", no_vision)
    result = await DescribeScreenTool().execute()
    assert result.success is False and "Bilder" in result.error
