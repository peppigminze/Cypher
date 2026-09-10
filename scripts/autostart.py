#!/usr/bin/env python3
"""
Windows autostart for Cypher via a per-user logon Scheduled Task
(project spec section 24). Starts scripts/start_jarvis.ps1 (backend +
agent) a short delay after login.

Design note: registering a scheduled task needs a one-time elevation
(UAC prompt) even for a per-user AtLogOn task targeting the current
user - verified live on Windows 11 (a plain, non-elevated
Register-ScheduledTask call fails with "Zugriff verweigert" / access
denied). install() therefore always goes through a UAC prompt; a
declined prompt is reported as a clean failure, not an exception.
Reading (status()) and removing (uninstall()) do not need elevation.

The script-builder functions (build_*_script, parse_task_query) are
pure - given a task name and paths, they return/parse plain text with
no side effects, so they are fully unit-tested without touching the
real Task Scheduler (see backend/tests/test_autostart.py). Only
install()/status()/uninstall() actually shell out, and are meant to be
run interactively via the CLI below - never silently by the app itself,
since registering a scheduled task is a persistent system change the
user should explicitly ask for.

Usage (from the project root, after the normal setup in README.md):
    python scripts/autostart.py install
    python scripts/autostart.py status
    python scripts/autostart.py uninstall
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TASK_NAME = "JARVIS Autostart"
_LOGON_DELAY_SECONDS = 20
_QUERY_SENTINEL = "<<<JARVIS_TASK>>>"


def _ps_lit(value: object) -> str:
    """Escape `value` for embedding in a single-quoted PowerShell
    literal - without this, an apostrophe anywhere in a path (a login
    like O'Brien, a folder named "Silvan's Projects") ends the literal
    early and the generated script fails to parse."""
    return str(value).replace("'", "''")


def build_register_task_script(task_name: str, launcher_script: Path, delay_seconds: int = _LOGON_DELAY_SECONDS) -> str:
    """Pure: the PowerShell that registers the logon task. No UAC/elevation -
    a per-user AtLogOn task for the current user doesn't require it."""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "  $action = New-ScheduledTaskAction -Execute 'powershell.exe' "
        "-Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
        f"-File \"{_ps_lit(launcher_script)}\"'\n"
        "  $trigger = New-ScheduledTaskTrigger -AtLogOn\n"
        f"  $trigger.Delay = 'PT{int(delay_seconds)}S'\n"
        "  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew\n"
        f"  Register-ScheduledTask -TaskName '{_ps_lit(task_name)}' -Action $action "
        "-Trigger $trigger -Settings $settings "
        "-Description 'Starts the Cypher backend and local PC agent at login.' -Force | Out-Null\n"
        "  exit 0\n"
        "} catch { Write-Error $_; exit 1 }\n"
    )


def build_query_task_script(task_name: str) -> str:
    """Pure: PowerShell that prints the task's action + state via sentinels."""
    return (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        f"$t = Get-ScheduledTask -TaskName '{_ps_lit(task_name)}' | Select-Object -First 1\n"
        "if ($t) {\n"
        "  $a = $t.Actions | Select-Object -First 1\n"
        f"  Write-Output ('{_QUERY_SENTINEL}' + $a.Execute)\n"
        f"  Write-Output ('{_QUERY_SENTINEL}' + $a.Arguments)\n"
        f"  Write-Output ('{_QUERY_SENTINEL}' + $t.State)\n"
        "}\n"
    )


def build_unregister_task_script(task_name: str) -> str:
    """Pure: PowerShell that removes the task (idempotent - no error if absent)."""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        f"  Unregister-ScheduledTask -TaskName '{_ps_lit(task_name)}' -Confirm:$false "
        "-ErrorAction SilentlyContinue\n"
        "  exit 0\n"
        "} catch { exit 1 }\n"
    )


def parse_task_query(stdout: str) -> dict | None:
    """Pure: parse build_query_task_script's output. None if the task is absent."""
    fields = [
        line[len(_QUERY_SENTINEL):]
        for line in stdout.splitlines()
        if line.startswith(_QUERY_SENTINEL)
    ]
    if len(fields) < 2:
        return None
    return {
        "execute": fields[0],
        "arguments": fields[1],
        "enabled": (fields[2].strip().lower() != "disabled") if len(fields) > 2 else True,
    }


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_powershell_elevated(script: str) -> bool:
    """Run `script` elevated via a one-time UAC prompt. True on success,
    False on a declined prompt or any failure - never raises, since a
    declined UAC prompt is an expected outcome, not an error."""
    fd, path = tempfile.mkstemp(suffix=".ps1")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
            fh.write(script)
        safe_path = path.replace("'", "''")
        launcher = (
            "$ErrorActionPreference = 'Stop'\n"
            "try {\n"
            "  $p = Start-Process -FilePath powershell -ArgumentList "
            "@('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden',"
            f"'-File','\"{safe_path}\"') -Verb RunAs -Wait -PassThru\n"
            "  if ($null -eq $p -or $null -eq $p.ExitCode) { exit 1 }\n"
            "  exit $p.ExitCode\n"
            "} catch { exit 1 }\n"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", launcher],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001 - declined UAC / launch failure -> False
        return False
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


def _launcher_script_path() -> Path:
    return Path(__file__).resolve().parent / "start_jarvis.ps1"


def status() -> dict | None:
    result = _run_powershell(build_query_task_script(TASK_NAME))
    return parse_task_query(result.stdout)


def install() -> bool:
    """Registers the logon task. Triggers a UAC prompt - the user must
    approve it in the dialog that pops up."""
    script = build_register_task_script(TASK_NAME, _launcher_script_path())
    return _run_powershell_elevated(script)


def uninstall() -> bool:
    result = _run_powershell(build_unregister_task_script(TASK_NAME))
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage Cypher's Windows logon autostart task.")
    parser.add_argument("command", choices=["install", "status", "uninstall"])
    args = parser.parse_args(argv)

    if args.command == "install":
        print("Registering the autostart task needs a one-time admin confirmation (UAC prompt)...")
        if install():
            print(f"Autostart task '{TASK_NAME}' registered - Cypher starts a few seconds after login.")
            return 0
        print("Could not register the autostart task (UAC prompt declined, or another error).", file=sys.stderr)
        return 1

    if args.command == "uninstall":
        if uninstall():
            print(f"Autostart task '{TASK_NAME}' removed.")
            return 0
        print("Could not remove the autostart task.", file=sys.stderr)
        return 1

    info = status()
    if info is None:
        print("No autostart task installed.")
    else:
        state = "enabled" if info["enabled"] else "disabled (won't fire)"
        print(f"Autostart task '{TASK_NAME}': {state}\n  {info['execute']} {info['arguments']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
