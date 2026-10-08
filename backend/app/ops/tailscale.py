"""
Read-only Tailscale helpers (phone access, docs/MOBILE.md).

`tailscale serve --bg <port>` publishes Cypher inside the tailnet as
https://<pc>.<tailnet>.ts.net with a real certificate - which the phone
needs for the microphone. The backend itself stays on 127.0.0.1; the
Tailscale daemon terminates HTTPS and forwards to it (keeping the Host
header and adding X-Forwarded-For/-Host/-Proto and Tailscale-User-*
headers - checked against tailscale's ipn/ipnlocal/serve.go).

Nothing here changes Tailscale's configuration; setting up `serve` is a
deliberate step by the user (it publishes the machine name in public
certificate transparency logs, see docs/MOBILE.md).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

_DEFAULT_EXE = Path(r"C:\Program Files\Tailscale\tailscale.exe")
_CACHE_SECONDS = 30.0
_cache: dict[int, tuple[float, Optional[str]]] = {}


def cli_path() -> Optional[str]:
    found = shutil.which("tailscale")
    if found:
        return found
    return str(_DEFAULT_EXE) if _DEFAULT_EXE.exists() else None


def _run(args: list[str]) -> Optional[str]:
    exe = cli_path()
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def backend_state() -> Optional[str]:
    """"Running", "Stopped", "NeedsLogin", ... or None if not installed."""
    raw = _run(["status", "--json"])
    try:
        return json.loads(raw).get("BackendState") if raw else None
    except (ValueError, AttributeError):
        return None


def serves_port(serve_status: str, port: int) -> bool:
    """Pure: does `tailscale serve status` output proxy to our local port?"""
    # e.g. "https://pc.tailnet.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:8000"
    return any(target in serve_status for target in (f"127.0.0.1:{port}", f"localhost:{port}"))


def https_url_from_status(status_json: str) -> Optional[str]:
    """Pure: https://<pc>.<tailnet>.ts.net from `tailscale status --json`."""
    try:
        name = (json.loads(status_json).get("Self") or {}).get("DNSName") or ""
    except (ValueError, AttributeError):
        return None
    name = name.rstrip(".")
    return f"https://{name}" if name else None


def serve_url(port: int) -> Optional[str]:
    """The HTTPS address the phone can use, or None if `tailscale serve`
    isn't set up for this port. Cached for 30 s (it runs two CLI calls)."""
    now = time.monotonic()
    cached = _cache.get(port)
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    url: Optional[str] = None
    status = _run(["status", "--json"])
    if status:
        serve = _run(["serve", "status"]) or ""
        if serves_port(serve, port):
            url = https_url_from_status(status)
    _cache[port] = (now, url)
    return url
