#!/usr/bin/env python3
"""
Windows autostart for Cypher via a per-user logon Scheduled Task
(project spec section 24). Starts scripts/start_cypher.ps1 -NoBrowser
(Ollama if needed + backend, hidden) a short delay after login.

install() also removes the task of the old name "JARVIS Autostart" (it
pointed at start_jarvis.ps1, which now only forwards to start_cypher.ps1).

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

TASK_NAME = "Cypher Autostart"
LEGACY_TASK_NAMES = ("JARVIS Autostart",)
_LAUNCHER_ARGS = "-NoBrowser"
_LOGON_DELAY_SECONDS = 20
_QUERY_SENTINEL = "<<<JARVIS_TASK>>>"


def _ps_lit(value: object) -> str:
    """Escape `value` for embedding in a single-quoted PowerShell
    literal - without this, an apostrophe anywhere in a path (a login
    like O'Brien, a folder named "Silvan's Projects") ends the literal
    early and the generated script fails to parse."""
    return str(value).replace("'", "''")


def build_register_task_script(
    task_name: str,
    launcher_script: Path,
    delay_seconds: int = _LOGON_DELAY_SECONDS,
    launcher_args: str = "",
    replaces: tuple[str, ...] = (),
) -> str:
    """Pure: the PowerShell that registers the logon task (run elevated,
    see the module docstring). `replaces`: older task names removed first."""
    removals = "".join(
        f"  Unregister-ScheduledTask -TaskName '{_ps_lit(name)}' -Confirm:$false -ErrorAction SilentlyContinue\n"
        for name in replaces
    )
    args = f" {_ps_lit(launcher_args)}" if launcher_args else ""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        f"{removals}"
        "  $action = New-ScheduledTaskAction -Execute 'powershell.exe' "
        "-Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
        f"-File \"{_ps_lit(launcher_script)}\"{args}'\n"
        "  $trigger = New-ScheduledTaskTrigger -AtLogOn\n"
        f"  $trigger.Delay = 'PT{int(delay_seconds)}S'\n"
        "  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew\n"
        f"  Register-ScheduledTask -TaskName '{_ps_lit(task_name)}' -Action $action "
        "-Trigger $trigger -Settings $settings "
        "-Description 'Starts Cypher (Ollama + backend) at login.' -Force | Out-Null\n"
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
    return Path(__file__).resolve().parent / "start_cypher.ps1"


def status() -> dict | None:
    result = _run_powershell(build_query_task_script(TASK_NAME))
    return parse_task_query(result.stdout)


def install() -> bool:
    """Registers the logon task. Triggers a UAC prompt - the user must
    approve it in the dialog that pops up."""
    script = build_register_task_script(TASK_NAME, _launcher_script_path(), launcher_args=_LAUNCHER_ARGS, replaces=LEGACY_TASK_NAMES)
    return _run_powershell_elevated(script)


def uninstall() -> bool:
    result = _run_powershell(build_unregister_task_script(TASK_NAME))
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cypher-Autostart bei der Windows-Anmeldung verwalten.")
    parser.add_argument("command", choices=["install", "status", "uninstall"])
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    if args.command == "install":
        print("Für die Autostart-Aufgabe fragt Windows einmal nach Administrator-Bestätigung (UAC) ...")
        if install():
            print(f"Aufgabe '{TASK_NAME}' eingerichtet - Cypher startet kurz nach der Anmeldung (ohne Browser).")
            return 0
        print("Autostart konnte nicht eingerichtet werden (UAC abgelehnt oder anderer Fehler).", file=sys.stderr)
        return 1

    if args.command == "uninstall":
        if uninstall():
            print(f"Aufgabe '{TASK_NAME}' entfernt.")
            return 0
        print("Autostart konnte nicht entfernt werden.", file=sys.stderr)
        return 1

    info = status()
    if info is None:
        print("Kein Autostart eingerichtet.")
        for legacy in LEGACY_TASK_NAMES:
            if parse_task_query(_run_powershell(build_query_task_script(legacy)).stdout):
                print(f"Alte Aufgabe '{legacy}' ist noch aktiv - 'install' ersetzt sie.")
    else:
        state = "aktiv" if info["enabled"] else "deaktiviert (startet nicht)"
        print(f"Aufgabe '{TASK_NAME}': {state}\n  {info['execute']} {info['arguments']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
