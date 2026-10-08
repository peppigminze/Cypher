"""
Stop Cypher cleanly (stop_cypher.bat).

1. POST /api/system/shutdown with the access token - the server runs its
   normal shutdown (conversations written, clients closed, final log line).
2. Wait until the port is free (up to 15 s).
3. Only if that fails: end the process from data/cypher.pid - or, for an
   older Cypher without pid file, the process on the port, but only if its
   command line shows it is the Cypher backend.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from typing import Optional, Tuple

import httpx

from app.config import get_settings
from app.security.tokens import get_tokens


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    settings = get_settings()
    port = settings.API_PORT
    if not _port_open(port):
        print("Cypher läuft nicht.")
        return 0
    accepted = False
    try:
        resp = httpx.post(f"http://127.0.0.1:{port}/api/system/shutdown", headers={"Authorization": f"Bearer {get_tokens(settings).user_token}"}, timeout=5.0)
        accepted = resp.status_code == 200
        if accepted:
            print("Cypher wird beendet …")
        elif resp.status_code == 404:
            print("Diese Cypher-Version kann sich noch nicht selbst beenden - beende den Prozess direkt.")
        else:
            print(f"Sauberes Beenden nicht möglich ({resp.status_code}): {resp.text[:200]}")
    except httpx.HTTPError as exc:
        print(f"Backend antwortet nicht ({exc}).")
    # Clean shutdown accepted: give it time (conversations are written last).
    deadline = time.monotonic() + (15 if accepted else 0)
    while time.monotonic() < deadline:
        if not _port_open(port):
            print("Cypher ist beendet.")
            return 0
        time.sleep(0.5)
    pid_file = settings.data_dir_path / "cypher.pid"
    if pid_file.exists():
        pid = int(pid_file.read_text(encoding="ascii").strip() or 0)
        if pid:
            print(f"Beende Prozess {pid} hart …")
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as exc:
                print(f"Konnte Prozess {pid} nicht beenden: {exc}")
                return 1
            time.sleep(1)
            if not _port_open(port):
                print("Cypher ist beendet.")
                return 0
    # No pid file (an older Cypher, e.g. from the former "JARVIS Autostart"
    # task): look up who owns the port - but only end it if it is Cypher.
    owner = _port_owner(port)
    if owner and is_cypher_backend(owner[1]):
        pid = owner[0]
        print(f"Beende älteren Cypher-Prozess {pid} …")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            print(f"Konnte Prozess {pid} nicht beenden: {exc}")
            return 1
        time.sleep(1)
        if not _port_open(port):
            print("Cypher ist beendet.")
            return 0
    elif owner:
        print(f"Port {port} gehört nicht zu Cypher ({owner[1][:120]}) - nichts beendet.")
        return 1
    print(f"Port {port} ist noch belegt - läuft Cypher ausserhalb von start_cypher.bat? Bitte das Fenster schliessen.")
    return 1


def is_cypher_backend(command_line: str) -> bool:
    """Pure: does this command line belong to the Cypher backend?"""
    cmd = command_line.replace("\\", "/").lower()
    if "app.main:app" in cmd or "backend/run.py" in cmd:  # uvicorn directly / run.py by path
        return True
    # run.py started inside backend/ with the project's venv (start_cypher.ps1)
    return "backend/.venv/" in cmd and cmd.rstrip('" ').endswith("run.py")


def _port_owner(port: int) -> Optional[Tuple[int, str]]:
    """(pid, command line) of the process listening on `port` (Windows)."""
    if sys.platform != "win32":
        return None
    script = (
        f"$c = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if ($c) { $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\"; "
        "Write-Output $c.OwningProcess; Write-Output $p.CommandLine }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].isdigit():
        return None
    return int(lines[0]), lines[1]


if __name__ == "__main__":
    sys.exit(main())
