"""
Tests for the pure script-builders/parser in scripts/autostart.py
(project spec section 24). Nothing here touches the real Task
Scheduler - install()/status()/uninstall() are live and deliberately
left uncovered; see README.md for how to exercise them manually.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from autostart import (  # noqa: E402
    TASK_NAME,
    build_query_task_script,
    build_register_task_script,
    build_unregister_task_script,
    parse_task_query,
)


def test_register_script_contains_task_name_and_launcher_path():
    script = build_register_task_script(TASK_NAME, Path("C:/JARVIS/scripts/start_jarvis.ps1"))
    assert TASK_NAME in script
    assert "start_jarvis.ps1" in script
    assert "AtLogOn" in script
    assert "Register-ScheduledTask" in script


def test_register_script_escapes_apostrophes_in_path():
    tricky_path = Path("C:/Users/O'Brien/JARVIS/scripts/start_jarvis.ps1")
    script = build_register_task_script(TASK_NAME, tricky_path)
    # The literal apostrophe must be doubled, not left to break the
    # single-quoted PowerShell string it's embedded in.
    assert "O''Brien" in script


def test_register_script_escapes_apostrophes_in_task_name():
    script = build_register_task_script("Silvan's JARVIS", Path("C:/x/start_jarvis.ps1"))
    assert "Silvan''s JARVIS" in script


def test_unregister_script_contains_task_name():
    script = build_unregister_task_script(TASK_NAME)
    assert TASK_NAME in script
    assert "Unregister-ScheduledTask" in script


def test_query_script_contains_sentinel_and_task_name():
    script = build_query_task_script(TASK_NAME)
    assert TASK_NAME in script
    assert "<<<JARVIS_TASK>>>" in script


@pytest.mark.parametrize(
    "stdout,expected",
    [
        ("", None),
        ("<<<JARVIS_TASK>>>powershell.exe", None),  # only one field - incomplete
    ],
)
def test_parse_task_query_absent_or_incomplete(stdout, expected):
    assert parse_task_query(stdout) == expected


def test_parse_task_query_enabled_task():
    stdout = (
        "<<<JARVIS_TASK>>>powershell.exe\n"
        "<<<JARVIS_TASK>>>-File \"C:\\JARVIS\\scripts\\start_jarvis.ps1\"\n"
        "<<<JARVIS_TASK>>>Ready\n"
    )
    info = parse_task_query(stdout)
    assert info is not None
    assert info["execute"] == "powershell.exe"
    assert info["enabled"] is True


def test_parse_task_query_disabled_task():
    stdout = "<<<JARVIS_TASK>>>powershell.exe\n<<<JARVIS_TASK>>>-File x\n<<<JARVIS_TASK>>>Disabled\n"
    info = parse_task_query(stdout)
    assert info["enabled"] is False


def test_parse_task_query_missing_state_field_defaults_to_enabled():
    """A read-back without the state field must not be treated as
    disabled - only an explicit 'Disabled' switches it off, matching
    the fail-safe behavior a scheduled task actually has."""
    stdout = "<<<JARVIS_TASK>>>powershell.exe\n<<<JARVIS_TASK>>>-File x\n"
    info = parse_task_query(stdout)
    assert info["enabled"] is True


def test_register_script_passes_launcher_args_and_replaces_legacy_task():
    from autostart import LEGACY_TASK_NAMES

    script = build_register_task_script(
        TASK_NAME, Path("C:/Cypher/scripts/start_cypher.ps1"), launcher_args="-NoBrowser", replaces=LEGACY_TASK_NAMES
    )
    assert "start_cypher.ps1\" -NoBrowser'" in script
    # the old task is removed before the new one is registered
    assert script.index("Unregister-ScheduledTask -TaskName 'JARVIS Autostart'") < script.index("Register-ScheduledTask -TaskName 'Cypher Autostart'")


def test_launcher_is_the_new_start_script():
    from autostart import _launcher_script_path

    assert TASK_NAME == "Cypher Autostart"
    assert _launcher_script_path().name == "start_cypher.ps1"
    assert _launcher_script_path().exists()
