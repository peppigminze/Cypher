"""
Alias table for app words (FIX2 point 3).

"Mach mir den Editor auf" must open Notepad, not whatever the model links
to "Editor" (qwen3.5:4b opened VS Code). Generic words are looked up HERE,
deterministically: open_app checks this table before the free Start-menu
search (app/pc/app_index.py), and a short "open X" utterance is resolved
before the model is asked at all (app/agent/quick_commands.py).

    lookup(word) -> Alias | None    exact match after app_index.normalize
    table()      -> {word: Alias}   built-in table + .env

Configurable: APP_ALIASES in .env adds or overrides entries
("editor=app:Notepad++,mail=outlook.exe"). APP_WHITELIST, the old name,
is still read; APP_ALIASES wins. A word that is not in the table is not an
alias - exact app names ("Visual Studio Code") go to the Start-menu index.

Targets:
    notepad.exe        a program (Windows finds it via PATH / App Paths)
    ms-settings:       a URI;  https://...  a web page
    app:<name>         the Start-menu entry with exactly this name
    @standardbrowser   the default browser itself (read from the registry)
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, Optional

from app.pc.app_index import normalize

DEFAULT_BROWSER = "@standardbrowser"
APP_PREFIX = "app:"


@dataclass(frozen=True)
class Alias:
    word: str
    target: str
    label: str  # how Cypher names it in a reply


# (words, target, label)
_BUILTIN = (
    (("editor", "texteditor", "text-editor", "notizblock", "notepad"), "notepad.exe", "Editor"),
    (("rechner", "taschenrechner", "calculator"), "calc.exe", "Rechner"),
    (("browser", "webbrowser", "standardbrowser", "internet"), DEFAULT_BROWSER, "Browser"),
    (("explorer", "datei-explorer", "dateiexplorer", "file explorer", "dateimanager"), "explorer.exe", "Explorer"),
    (("taskmanager", "task-manager", "task manager"), "taskmgr.exe", "Task-Manager"),
    (("einstellungen", "windows-einstellungen", "systemeinstellungen", "settings"), "ms-settings:", "Einstellungen"),
    (("systemsteuerung", "control panel"), "control.exe", "Systemsteuerung"),
    (("vs code", "vscode"), "app:Visual Studio Code", "Visual Studio Code"),
    (("spotify",), "spotify:", "Spotify"),
)


def table() -> Dict[str, Alias]:
    from app.config import get_settings  # noqa: PLC0415

    out: Dict[str, Alias] = {}
    for words, target, label in _BUILTIN:
        for word in words:
            out[normalize(word)] = Alias(word, target, label)
    for word, target in get_settings().app_alias_map.items():
        key = normalize(word)
        if key:
            out[key] = Alias(word, target, word[:1].upper() + word[1:])
    return out


def lookup(text: str) -> Optional[Alias]:
    key = normalize(text)
    return table().get(key) if key else None


# ------------------------------------------------------------------ default browser
def _browser_command() -> Optional[str]:
    """The open command of the program registered for https (then http)."""
    if sys.platform != "win32":
        return None
    import winreg  # noqa: PLC0415

    for scheme in ("https", "http"):
        for choice in ("UserChoiceLatest", "UserChoice"):  # newer Windows 11 builds write ...Latest
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{scheme}\{choice}") as key:
                    prog_id = winreg.QueryValueEx(key, "ProgId")[0]
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
                    return winreg.QueryValueEx(key, "")[0]
            except OSError:
                continue
    return None


def exe_from_command(command: str) -> str:
    """'"C:\\...\\chrome.exe" --single-argument %1' -> 'C:\\...\\chrome.exe'."""
    text = os.path.expandvars((command or "").strip())
    match = re.match(r'^"([^"]+)"', text) or re.match(r"^(.+?\.exe)(?:\s|$)", text, re.IGNORECASE)
    return match.group(1) if match and match.group(1).lower().endswith(".exe") else ""


def default_browser() -> Optional[str]:
    """Path of the default browser's .exe, or None if Windows has none set."""
    exe = exe_from_command(_browser_command() or "")
    return exe if exe and os.path.isfile(exe) else None
