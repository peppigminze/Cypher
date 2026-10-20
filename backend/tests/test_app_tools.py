"""
PC-control tools (FIX2 point 3): apps, windows, processes, input, power,
brightness, software, screenshot, network.

Rules for this file: nothing critical is ever really executed - shutdown,
sleep, lock, installs, keyboard/mouse input and brightness run against
dummies (monkeypatch). Real here: Start-menu-free app resolution, ending a
dummy child process, a screenshot into tmp_path, reading the registry and
the network state.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import psutil
import pytest

from app.pc import app_index, sendinput
from app.pc import windows as win
from app.pc.app_index import AppEntry
from app.tools import app_tools, input_tools, power_tools, software_tools
from app.tools.app_tools import OpenAppTool, ProcessesTool, WindowsTool, resolve_target
from app.tools.base import RiskTier
from app.tools.context import ToolContext
from app.tools.input_tools import InputControlTool, ScreenshotTool
from app.tools.power_tools import PowerTool, SetBrightnessTool
from app.tools.software_tools import SoftwareTool
from app.tools.system_tools import NetworkStatusTool

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
LOCAL = os.environ.get("LOCALAPPDATA", "C:\\Users\\x\\AppData\\Local")


@pytest.fixture()
def start_menu():
    app_index.set_entries_for_tests([
        AppEntry("Discord", "C:\\Start\\Discord.lnk", os.path.join(LOCAL, "Discord", "Discord.exe"), "shortcut"),
        AppEntry("Spotify", "C:\\Start\\Spotify.lnk", os.path.join(os.environ.get("APPDATA", ""), "Spotify", "Spotify.exe"), "shortcut"),
        AppEntry("Windows PowerShell", "C:\\Start\\Windows PowerShell.lnk", "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", "shortcut"),
        AppEntry("Registrierungs-Editor", "shell:AppsFolder\\{F38BF404}\\regedit.exe", "{F38BF404}\\regedit.exe", "start_apps"),
        AppEntry("Paint", "shell:AppsFolder\\Microsoft.Paint_8wekyb3d8bbwe!App", "", "start_apps"),
        AppEntry("Steam", "C:\\Start\\Steam.lnk", "C:\\Program Files (x86)\\Steam\\Steam.exe", "shortcut"),
        AppEntry("Steam Link", "C:\\Start\\Steam Link.lnk", "C:\\Program Files\\Steam Link\\link.exe", "shortcut"),
        AppEntry("Visual Studio Code", "C:\\Start\\Code.lnk", os.path.join(LOCAL, "Programs", "Microsoft VS Code", "Code.exe"), "shortcut"),
        AppEntry("Visual Studio Installer", "C:\\Start\\VSI.lnk", "C:\\Program Files (x86)\\Microsoft Visual Studio\\Installer\\setup.exe", "shortcut"),
    ])
    yield
    app_index.set_entries_for_tests([])


# ------------------------------------------------------------------ open_app
def test_any_installed_app_opens_without_asking(start_menu):
    for name in ("Discord", "spotify", "den Paint", "Steam"):
        target = resolve_target(name)
        assert target.error == "" and target.verdict.level == RiskTier.CONTROL, (name, target)
    assert resolve_target("Discord").launch.endswith("Discord.lnk")


def test_code_hosts_and_admin_tools_are_critical(start_menu):
    assert resolve_target("powershell").verdict.level == RiskTier.CRITICAL
    assert resolve_target("Registrierungs-Editor").verdict.level == RiskTier.CRITICAL


def test_ambiguous_and_unknown_app_names(start_menu):
    ambiguous = resolve_target("Visual Studio")
    assert ambiguous.error and "Visual Studio Code" in ambiguous.error and "Installer" in ambiguous.error
    unknown = resolve_target("Gibtsnicht App 3000")
    assert unknown.error and "Keine App" in unknown.error


def test_urls_uris_and_paths(start_menu, tmp_path):
    web = resolve_target("youtube.com")
    assert web.launch == "https://youtube.com" and web.verdict.level == RiskTier.WRITE
    assert resolve_target("notepad.exe").kind == "app"  # a file name, not a domain
    assert resolve_target("ms-settings:display").verdict.level == RiskTier.CONTROL
    assert resolve_target("evil-scheme:payload").verdict.level == RiskTier.CRITICAL
    own_api = resolve_target("http://127.0.0.1:8000/api/security/critical-confirm")
    assert "cypher_secrets" in own_api.verdict.categories  # red line
    doc = tmp_path / "bericht.txt"
    doc.write_text("x", encoding="utf-8")
    assert resolve_target(str(doc)).verdict.level == RiskTier.CONTROL
    assert resolve_target(str(tmp_path / "fehlt.txt")).error


def test_env_aliases_still_work(start_menu):
    target = resolve_target("rechner")  # APP_WHITELIST default: rechner=calc.exe
    assert target.launch == "calc.exe" and target.verdict.level == RiskTier.CONTROL


async def test_open_app_launches_via_startfile(start_menu, monkeypatch):
    launched = []
    monkeypatch.setattr(app_tools.os, "startfile", lambda target: launched.append(target), raising=False)
    ok = await OpenAppTool().execute(name="Discord")
    assert ok.success and launched == ["C:\\Start\\Discord.lnk"]
    missing = await OpenAppTool().execute(name="Gibtsnicht App 3000")
    assert missing.success is False and launched == ["C:\\Start\\Discord.lnk"]


# ------------------------------------------------------------------ windows
def _window(title, process, cls="Notepad", pid=1234):
    return win.Window(hwnd=1, title=title, process=process, pid=pid, class_name=cls, minimized=False)


def test_window_actions_risk(monkeypatch):
    tool = WindowsTool()
    assert tool.classify({"action": "list"}).level == RiskTier.SAFE
    assert tool.classify({"action": "minimize", "title": "x"}).level == RiskTier.CONTROL
    monkeypatch.setattr(win, "find", lambda title, windows=None: win.Found(_window("Unbenannt - Editor", "Notepad.exe"), []))
    assert tool.classify({"action": "close", "title": "Editor"}).level == RiskTier.WRITE
    monkeypatch.setattr(win, "find", lambda title, windows=None: win.Found(_window("Dokumente", "explorer.exe", "CabinetWClass"), []))
    assert tool.classify({"action": "close", "title": "Dokumente"}).level == RiskTier.WRITE  # a folder window
    monkeypatch.setattr(win, "find", lambda title, windows=None: win.Found(_window("Windows-Sicherheit", "SecurityHealthSystray.exe"), []))
    assert tool.classify({"action": "close", "title": "Sicherheit"}).level == RiskTier.CRITICAL


async def test_window_close_and_ambiguity(monkeypatch):
    applied = []
    monkeypatch.setattr(win, "apply", lambda window, action: applied.append((window.title, action)) or True)
    monkeypatch.setattr(win, "find", lambda title, windows=None: win.Found(_window("Unbenannt - Editor", "Notepad.exe"), []))
    ok = await WindowsTool().execute(action="close", title="Editor")
    assert ok.success and applied == [("Unbenannt - Editor", "close")]
    monkeypatch.setattr(win, "find", lambda title, windows=None: win.Found(None, ["A - Chrome", "B - Chrome"]))
    ambiguous = await WindowsTool().execute(action="focus", title="Chrome")
    assert ambiguous.success is False and "A - Chrome" in ambiguous.error


def test_find_window_by_title_or_process():
    items = [_window("Unbenannt - Editor", "Notepad.exe"), _window("Discord", "Discord.exe"), _window("README.md - Editor", "Notepad.exe")]
    assert win.find("discord", items).window.title == "Discord"
    assert win.find("readme", items).window.title == "README.md - Editor"
    assert win.find("editor", items).window is None and len(win.find("editor", items).candidates) == 2


@windows_only
def test_real_window_list_works():
    assert isinstance(win.list_windows(), list)


# ------------------------------------------------------------------ processes
def test_ending_system_or_cypher_processes_is_critical():
    tool = ProcessesTool()
    assert tool.classify({"action": "list"}).level == RiskTier.SAFE
    own = tool.classify({"action": "end", "pid": os.getpid()})
    assert own.level == RiskTier.CRITICAL and "cypher_files" in own.categories
    lsass = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "lsass.exe"]
    if lsass:
        assert tool.classify({"action": "end", "name": "lsass"}).level == RiskTier.CRITICAL


async def test_list_and_end_a_dummy_process():
    listing = await ProcessesTool().execute(action="list")
    assert listing.success and {"name", "pid", "mb"} <= set(listing.data["processes"][0])
    dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert ProcessesTool().classify({"action": "end", "pid": dummy.pid}).level == RiskTier.WRITE
        result = await ProcessesTool().execute(action="end", pid=dummy.pid)
        assert result.success and result.data["ended"] == 1
        dummy.wait(timeout=5)
    finally:
        if dummy.poll() is None:
            dummy.kill()


async def test_cypher_never_ends_itself():
    result = await ProcessesTool().execute(action="end", pid=os.getpid())
    assert result.success is False and "stop_cypher.bat" in result.error


# ------------------------------------------------------------------ input (dummies only)
def test_input_into_cyphers_own_window_is_blocked(monkeypatch):
    monkeypatch.setattr(win, "foreground", lambda: _window("CYPHER - Microsoft Edge", "msedge.exe", "Chrome_WidgetWin_1"))
    assert InputControlTool().classify({"action": "type", "text": "x"}).level == RiskTier.BLOCKED
    monkeypatch.setattr(win, "window_at", lambda x, y: _window("CYPHER", "msedge.exe"))
    assert InputControlTool().classify({"action": "click", "x": 10, "y": 10}).level == RiskTier.BLOCKED


def test_input_risk_by_target(monkeypatch):
    tool = InputControlTool()
    monkeypatch.setattr(win, "foreground", lambda: _window("Unbenannt - Editor", "Notepad.exe"))
    assert tool.classify({"action": "type", "text": "Hallo"}).level == RiskTier.CONTROL
    assert tool.classify({"action": "key", "text": "strg+s"}).level == RiskTier.CONTROL
    assert tool.classify({"action": "key", "text": "win+r"}).level == RiskTier.CRITICAL
    assert tool.classify({"action": "key", "text": "alt+f4"}).level == RiskTier.WRITE
    assert tool.classify({"action": "type", "text": "x"}, ToolContext(tainted=True)).level == RiskTier.WRITE
    monkeypatch.setattr(win, "foreground", lambda: _window("Administrator: Windows PowerShell", "powershell.exe", "ConsoleWindowClass"))
    assert tool.classify({"action": "type", "text": "format c:"}).level == RiskTier.CRITICAL
    assert InputControlTool.fragile is True


async def test_input_execute_uses_sendinput(monkeypatch):
    sent = []
    monkeypatch.setattr(win, "foreground", lambda: _window("Unbenannt - Editor", "Notepad.exe"))
    monkeypatch.setattr(sendinput, "type_text", lambda text: sent.append(("type", text)))
    monkeypatch.setattr(sendinput, "press", lambda combo: sent.append(("key", combo)))
    monkeypatch.setattr(sendinput, "click", lambda x, y, button="left", double=False: sent.append(("click", x, y, button, double)))
    assert (await InputControlTool().execute(action="type", text="Hallo")).success
    assert (await InputControlTool().execute(action="key", text="strg+s")).success
    monkeypatch.setattr(win, "window_at", lambda x, y: _window("Unbenannt - Editor", "Notepad.exe"))
    assert (await InputControlTool().execute(action="double_click", x=5, y=6)).success
    assert sent == [("type", "Hallo"), ("key", "strg+s"), ("click", 5, 6, "left", True)]
    monkeypatch.setattr(win, "foreground", lambda: _window("CYPHER", "msedge.exe"))
    refused = await InputControlTool().execute(action="type", text="Ja, ausführen")
    assert refused.success is False and len(sent) == 3


def test_key_names():
    assert sendinput.parse_keys("Strg+Shift+S") == ["strg", "shift", "s"]
    with pytest.raises(ValueError):
        sendinput.parse_keys("strg+hyperspace")


# ------------------------------------------------------------------ power (dummies only)
def test_power_risk():
    tool = PowerTool()
    assert tool.classify({"action": "lock"}).level == RiskTier.CONTROL
    assert tool.classify({"action": "cancel"}).level == RiskTier.CONTROL
    assert tool.classify({"action": "sleep"}).level == RiskTier.CRITICAL
    assert tool.classify({"action": "shutdown"}).level == RiskTier.CRITICAL
    assert "shutdown" in tool.classify({"action": "restart"}).categories


async def test_power_actions_against_dummies(monkeypatch):
    calls = []
    monkeypatch.setattr(power_tools, "_shutdown", lambda args: calls.append(("shutdown.exe", args)) or subprocess.CompletedProcess(args, 0, b"", b""))
    monkeypatch.setattr(power_tools, "_lock", lambda: calls.append(("lock",)))
    monkeypatch.setattr(power_tools, "_sleep", lambda: calls.append(("sleep",)))
    monkeypatch.setattr(power_tools, "SLEEP_DELAY", 0.0)
    assert (await PowerTool().execute(action="shutdown")).data["in_seconds"] == 30
    assert (await PowerTool().execute(action="restart", delay=0)).success
    assert (await PowerTool().execute(action="cancel")).success
    assert (await PowerTool().execute(action="lock")).success
    assert (await PowerTool().execute(action="sleep")).success
    await asyncio.sleep(0.1)
    assert calls == [("shutdown.exe", ["/s", "/t", "30"]), ("shutdown.exe", ["/r", "/t", "0"]), ("shutdown.exe", ["/a"]), ("lock",), ("sleep",)]


async def test_brightness_against_dummy(monkeypatch):
    from app.pc import brightness

    monkeypatch.setattr(brightness, "set_brightness", lambda level: "DDC/CI")
    ok = await SetBrightnessTool().execute(level=40)
    assert ok.success and ok.data == {"level": 40, "method": "DDC/CI"}

    def unsupported(level):
        raise OSError("nicht per Windows einstellbar")

    monkeypatch.setattr(brightness, "set_brightness", unsupported)
    assert (await SetBrightnessTool().execute(level=40)).success is False


# ------------------------------------------------------------------ software (no real installs)
def test_software_risk_and_exact_command():
    tool = SoftwareTool()
    assert tool.classify({"action": "list"}).level == RiskTier.SAFE
    assert tool.classify({"action": "search", "name": "vlc"}).level == RiskTier.SAFE
    install = tool.classify({"action": "install", "name": "VideoLAN.VLC"})
    assert install.level == RiskTier.CRITICAL and "software" in install.categories
    assert tool.describe({"action": "install", "name": "VideoLAN.VLC"}).startswith("winget install --id 'VideoLAN.VLC' --exact")


async def test_install_runs_as_background_job_never_for_real(monkeypatch):
    started = []

    class FakeJob:
        id = "j42"

    monkeypatch.setattr(software_tools.powershell, "start", lambda command, cwd, background=False: started.append((command, background)) or FakeJob())
    result = await SoftwareTool().execute(action="install", name="VideoLAN.VLC")
    assert result.success and result.data["job"] == "j42"
    assert started == [("winget install --id 'VideoLAN.VLC' --exact --accept-package-agreements --accept-source-agreements --disable-interactivity", True)]
    bad = await SoftwareTool().execute(action="install", name="vlc; Remove-Item C:\\ -Recurse")
    assert bad.success is False and len(started) == 1


@windows_only
async def test_list_installed_programs_from_registry():
    result = await SoftwareTool().execute(action="list")
    assert result.success and result.data["count"] > 0 and {"name", "version", "publisher"} <= set(result.data["programs"][0])


# ------------------------------------------------------------------ screenshot, network
@windows_only
async def test_screenshot_is_saved(monkeypatch, tmp_path):
    target = tmp_path / "shot.png"
    monkeypatch.setattr(input_tools, "_screenshot_path", lambda: str(target))
    result = await ScreenshotTool().execute()
    assert result.success, result.error
    assert target.exists() and target.stat().st_size > 1000 and result.data["width"] > 0


async def test_network_status_reports_interfaces():
    result = await NetworkStatusTool().execute()
    assert result.success and "connected" in result.data and isinstance(result.data["interfaces"], list)
