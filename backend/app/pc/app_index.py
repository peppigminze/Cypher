"""
Start menu index: "öffne Discord" -> what to launch.

Two sources, read with ONE PowerShell call (about 1 s) and cached:
    shortcuts   *.lnk in both Start menu folders, with their target .exe
    Get-StartApps   every app Windows lists in Start, including Store apps
                    (AppUserModelID, launched via shell:AppsFolder\\<id>)

APP_WHITELIST from .env is no longer a whitelist (FIX2): its entries are
short names that win over the index ("rechner=calc.exe").

The index is refreshed after INDEX_TTL seconds; a failed refresh keeps
the old one.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.pc.powershell import CREATE_NO_WINDOW, clixml_to_text, powershell_args

logger = logging.getLogger("cypher.pc.app_index")

INDEX_TTL = 600.0
_SCRIPT = r"""
$sh = New-Object -ComObject WScript.Shell
$dirs = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs", "$env:ProgramData\Microsoft\Windows\Start Menu\Programs")
$links = foreach ($d in $dirs) { Get-ChildItem -LiteralPath $d -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{n=$_.BaseName; p=$_.FullName; t=$sh.CreateShortcut($_.FullName).TargetPath} } }
$apps = Get-StartApps | ForEach-Object { [pscustomobject]@{n=$_.Name; a=$_.AppID} }
@{links=@($links); apps=@($apps)} | ConvertTo-Json -Depth 3 -Compress
"""
# Shortcuts that are not apps.
_SKIP = re.compile(r"\b(uninstall|deinstall|readme|liesmich|hilfe|help|website|documentation|dokumentation|release notes)\b", re.IGNORECASE)
_FILLER = re.compile(r"\b(den|die|das|dem|mir|mal|bitte|app|programm|anwendung|starten|öffnen|oeffnen|open|start)\b")


@dataclass(frozen=True)
class AppEntry:
    name: str
    launch: str  # what os.startfile gets: .lnk path, shell:AppsFolder\<id>, exe, URI
    target: str  # the .exe behind it if known (for the risk classifier), else ""
    source: str  # "alias" | "shortcut" | "start_apps"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _FILLER.sub(" ", text)
    text = re.sub(r"[^\w\s+#.-]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


_lock = threading.Lock()
_cache: Dict[str, object] = {"at": 0.0, "entries": []}


def _load() -> List[AppEntry]:
    proc = subprocess.run(powershell_args(_SCRIPT), capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise OSError(clixml_to_text(proc.stderr.decode("utf-8", "replace"))[:300] or f"exit {proc.returncode}")
    data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    entries: List[AppEntry] = []
    by_name: Dict[str, AppEntry] = {}
    for item in data.get("links") or []:
        name, path, target = item.get("n") or "", item.get("p") or "", item.get("t") or ""
        if not name or _SKIP.search(name):
            continue
        entry = AppEntry(name=name, launch=path, target=target, source="shortcut")
        by_name.setdefault(normalize(name), entry)
    for item in data.get("apps") or []:
        name, aumid = item.get("n") or "", item.get("a") or ""
        if not name or not aumid or _SKIP.search(name):
            continue
        key = normalize(name)
        if key in by_name:
            continue  # the shortcut knows the real .exe - better for the classifier
        # Desktop apps without a shortcut report their exe path (with a known-folder GUID) as id.
        target = aumid if aumid.lower().endswith(".exe") else ""
        by_name[key] = AppEntry(name=name, launch=f"shell:AppsFolder\\{aumid}", target=target, source="start_apps")
    entries.extend(by_name.values())
    return entries


def entries(force: bool = False) -> List[AppEntry]:
    with _lock:
        fresh = time.monotonic() - float(_cache["at"]) < INDEX_TTL and _cache["entries"]
        if fresh and not force:
            return list(_cache["entries"])  # type: ignore[arg-type]
    try:
        loaded = _load()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.warning("Start menu index could not be read: %s", exc)
        with _lock:
            return list(_cache["entries"])  # type: ignore[arg-type]
    with _lock:
        _cache.update(at=time.monotonic(), entries=loaded)
    logger.info("Start menu index: %d apps", len(loaded))
    return loaded


def warm_up() -> None:
    threading.Thread(target=entries, daemon=True, name="app-index").start()


def set_entries_for_tests(items: List[AppEntry]) -> None:
    with _lock:
        _cache.update(at=time.monotonic(), entries=list(items))


def _aliases() -> List[AppEntry]:
    from app.config import get_settings  # noqa: PLC0415

    return [AppEntry(name=k, launch=v, target=v if v.lower().endswith(".exe") else "", source="alias") for k, v in get_settings().app_whitelist_map.items()]


@dataclass
class Match:
    entry: Optional[AppEntry]
    candidates: List[str]


def find(query: str) -> Match:
    """Best match for a spoken app name. Ambiguous -> candidates, no entry."""
    wanted = normalize(query)
    if not wanted:
        return Match(None, [])
    for alias in _aliases():
        if normalize(alias.name) == wanted:
            return Match(alias, [])
    items = entries()
    exact = [e for e in items if normalize(e.name) == wanted]
    if exact:
        return Match(exact[0], [])
    words = wanted.split()
    scored = []
    for entry in items:
        name = normalize(entry.name)
        name_words = name.split()
        if name.startswith(wanted) or all(any(nw.startswith(w) for nw in name_words) for w in words):
            scored.append((0 if name.startswith(wanted) else 1, len(name), entry))
    if not scored:
        return Match(None, [])
    scored.sort(key=lambda s: (s[0], s[1]))
    best = [s for s in scored if s[0] == scored[0][0]]
    if len(best) > 1:
        # "Visual Studio" -> Code or Installer? Ask instead of guessing.
        return Match(None, [s[2].name for s in best[:6]])
    return Match(best[0][2], [])
