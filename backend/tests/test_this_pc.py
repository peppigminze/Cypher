"""
This PC as a network target is on the red line (app/security/this_pc.py).

Follow-up to FIX2: `curl.exe … 127.0.0.1:8000` reached Cypher's API as a
mere `critical` (no question with the safety prompt off). Now any program,
any spelling of loopback, the configured backend address and the tailnet
identity count as `cypher_secrets`. Only the classifier runs here - no
command is executed, Tailscale and windows are stubs.
"""
from __future__ import annotations

import base64
import inspect
import json
import socket
import time

import pytest

from app.config import get_settings
from app.ops import tailscale
from app.pc import windows as win
from app.security import this_pc
from app.security.classifier import classify_open_target, classify_powershell
from app.tools.base import RiskTier
from app.tools.input_tools import InputControlTool

ENCODED = base64.b64encode("curl.exe http://127.0.0.1:8000/api/auth/session".encode("utf-16-le")).decode()
TAILNET = ("cypher-pc", "cypher-pc.tail1234.ts.net", "100.101.102.103", "fd7a:115c:a1e0::1234")


@pytest.fixture(autouse=True)
def no_tailnet():
    this_pc.set_tailscale_hosts((), pinned=True)
    this_pc._backend_cache.clear()
    yield
    this_pc.set_tailscale_hosts((), pinned=True)
    this_pc._backend_cache.clear()


@pytest.fixture()
def tailnet():
    this_pc.set_tailscale_hosts(TAILNET, pinned=True)


@pytest.fixture()
def api_host(monkeypatch):
    def set_host(host, port=8000):
        monkeypatch.setenv("API_HOST", host)
        monkeypatch.setenv("API_PORT", str(port))
        monkeypatch.setenv("USER_TOKEN", "t" * 40)  # an open API_HOST needs real tokens
        monkeypatch.setenv("AGENT_TOKEN", "a" * 40)
        get_settings.cache_clear()
        this_pc._backend_cache.clear()

    yield set_host
    get_settings.cache_clear()
    this_pc._backend_cache.clear()


def _red(command: str) -> bool:
    result = classify_powershell(command)  # critical, or blocked for other reasons (stricter)
    return result.level.rank >= RiskTier.CRITICAL.rank and "cypher_secrets" in result.categories


LOOPBACK = [
    "curl.exe -X PUT http://127.0.0.1:8000/api/security/critical-confirm -H 'Sec-Fetch-Site: same-origin'",
    "curl.exe http://localhost:8000/api/auth/session",
    "iwr http://LOCALHOST./api/health",  # upper case, trailing dot
    "wget.exe http://[::1]:8000/",
    "curl.exe http://[0:0:0:0:0:0:0:1]:8000/",
    "curl.exe http://[::ffff:127.0.0.1]:8000/",  # IPv4-mapped
    "curl.exe http://0x7f.1:8000/",  # hex, short form
    "curl.exe http://2130706433:8000/",  # one decimal number
    "curl.exe http://0177.0.0.1:8000/",  # octal
    "curl.exe http://127.1:8000/",
    "curl.exe http://127.42.0.9:8000/",  # all of 127.0.0.0/8
    "curl.exe http://0.0.0.0:8000/",
    "curl.exe http://[::]:8000/",
    "curl.exe http://api.localhost:8000/",
    "curl.exe http://user:pw@127.0.0.1:8000/",
    "curl.exe localhost:8000/api/auth/session",  # no scheme
    "curl.exe http://１２７．０．０．１:8000/",  # full-width digits
    "python -c \"import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/auth/session')\"",
    "node -e \"fetch('http://localhost:8000/api/auth/session')\"",
    "Invoke-RestMethod -Method Put http://127.0.0.1:8000/api/security/critical-confirm",
    "Start-Process msedge http://127.0.0.1:8000/",
    "Start-Process 'http://localhost:8000/'",
    "$u = 'http://127.0.0.1:8000'; curl.exe $u",  # variable
    "curl.exe ('http://127.0.0.' + '1:8000/api/auth/session')",  # concatenation
    "$h = ('127','0','0','1') -join '.'; curl.exe \"http://${h}:8000\"",  # -join
    "curl.exe \"http://local`host:8000/\"",  # backtick escape
    "curl.exe http://%31%32%37.0.0.1:8000/",  # URL escapes
    f"powershell -EncodedCommand {ENCODED}",  # base64 payload
    "curl.exe http://127.0.0.1:8000/api/auth/session > token.txt",  # redirection
    "Get-Content urls.txt | ForEach-Object { curl.exe http://127.0.0.1:8000$_ }",  # pipeline + script block
    "Get-ChildItem \\\\localhost\\c$",  # UNC
    "ping 127.0.0.1",  # also harmless programs: any reference counts
]


@pytest.mark.parametrize("command", LOOPBACK)
def test_this_pc_in_any_command_is_on_the_red_line(command):
    assert _red(command), (command, classify_powershell(command))


@pytest.mark.parametrize(
    "command",
    ["ping 8.8.8.8", "Invoke-WebRequest https://example.com", "Start-Sleep -Seconds 0.5", "[math]::Round(3.14)", "[Math]::Max(1, 2)",
     "Get-Date -Format 'HH:mm'", "Write-Output 'Version 10.0.19045'", "Test-Connection 192.168.1.1", "schtasks /query /st 00:30",
     "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5", "git status", "Get-ChildItem C:\\Users\\x\\AppData\\Local\\Programs"],
)
def test_harmless_commands_stay_below_critical(command):
    result = classify_powershell(command)
    assert result.level.rank < RiskTier.CRITICAL.rank and "cypher_secrets" not in result.categories, (command, result)


def test_unknown_programs_without_this_pc_stay_critical_but_not_red():
    for command in ("curl.exe https://example.com", "node -e \"fetch('https://example.com')\""):
        result = classify_powershell(command)
        assert result.level == RiskTier.CRITICAL and "cypher_secrets" not in result.categories, command


def test_unparsable_commands_are_still_scanned():
    # an unclosed quote is "nicht sicher einstufbar" - the address is still seen
    result = classify_powershell("curl.exe 'http://127.0.0.1:8000/api/auth/session")
    assert {"unclassifiable", "cypher_secrets"} <= set(result.categories)


def test_tailnet_name_and_ips(tailnet):
    for command in ("curl.exe https://cypher-pc.tail1234.ts.net/api/auth/session", "curl.exe http://cypher-pc/api/auth/session",
                    "curl.exe http://100.101.102.103/", "curl.exe http://[fd7a:115c:a1e0::1234]/"):
        assert _red(command), command
    assert not _red("curl.exe http://100.101.102.104/")  # another tailnet device


def test_configured_backend_address_with_its_port(api_host):
    api_host("192.168.1.50", 8000)
    assert _red("curl.exe http://192.168.1.50:8000/api/auth/session")
    assert not _red("curl.exe http://192.168.1.50/")  # port 80 is not Cypher
    api_host("0.0.0.0", 8123)  # all interfaces: the computer name reaches it too
    assert _red(f"curl.exe http://{socket.gethostname()}:8123/api/auth/session")
    assert not _red(f"curl.exe http://{socket.gethostname()}:9999/")


def test_open_app_and_typed_text(monkeypatch):
    for url in ("http://0x7f.1:8000/", "http://localhost./", "http://[::1]:8000/api/x"):
        assert "cypher_secrets" in classify_open_target(url).categories, url
    assert classify_open_target("https://example.com").level == RiskTier.WRITE
    terminal = win.Window(hwnd=1, title="Windows PowerShell", process="WindowsTerminal.exe", pid=1, class_name="X", minimized=False)
    browser = win.Window(hwnd=2, title="Neuer Tab", process="chrome.exe", pid=2, class_name="Chrome_WidgetWin_1", minimized=False)
    tool = InputControlTool()
    for window in (terminal, browser):
        monkeypatch.setattr(win, "foreground", lambda w=window: w)
        typed = tool.classify({"action": "type", "text": "curl.exe http://127.0.0.1:8000/api/auth/session"})
        assert "cypher_secrets" in typed.categories, window.process
    assert "cypher_secrets" not in tool.classify({"action": "type", "text": "Hallo Welt"}).categories


def test_tailscale_identity_parser():
    status = json.dumps({"Self": {"DNSName": "Cypher-PC.tail1234.ts.net.", "HostName": "CYPHER-PC", "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1234"]},
                         "CertDomains": ["cypher-pc.tail1234.ts.net"]})
    assert tailscale.self_identities(status) == frozenset({"cypher-pc.tail1234.ts.net", "cypher-pc", "100.101.102.103", "fd7a:115c:a1e0::1234"})
    assert tailscale.self_identities("kein json") == frozenset()


def test_tailnet_identity_is_read_in_the_background(monkeypatch):
    calls = []
    status = json.dumps({"Self": {"DNSName": "pc.tailnet.ts.net.", "TailscaleIPs": ["100.64.0.9"]}})
    monkeypatch.setattr(tailscale, "status_json", lambda: calls.append(1) or status)
    this_pc.warm_up()  # pinned (tests): no lookup at all
    assert calls == []
    with this_pc._lock:
        this_pc._tailscale.update(pinned=False, at=float("-inf"), hosts=frozenset(), running=False)
    this_pc.reference("curl.exe http://100.64.0.9/")  # stale: starts the lookup, does not wait for it
    for _ in range(100):
        if this_pc._tailscale["hosts"]:
            break
        time.sleep(0.02)
    assert calls == [1] and _red("curl.exe http://100.64.0.9/") and _red("curl.exe https://pc.tailnet.ts.net/")


def test_cyphers_internal_calls_do_not_go_through_the_classifier():
    """The exemption: Cypher's own requests (Ollama, health, Start menu index,
    brightness, Tailscale status) are not tool calls and never classified."""
    from app.core import health
    from app.llm import model_manager, ollama
    from app.pc import app_index, brightness

    for module in (ollama, model_manager, health, app_index, brightness, tailscale):
        source = inspect.getsource(module)
        assert "classify_powershell" not in source and "this_pc" not in source, module.__name__
