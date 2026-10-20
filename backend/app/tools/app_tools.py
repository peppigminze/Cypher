"""
Apps, windows and processes (FIX2 point 3).

    open_app    any installed app (Start menu index, no whitelist), a web
                page in the default browser, a URI (spotify:, ms-settings:),
                a file or folder. App words ("Editor", "Rechner", "Browser")
                come from the alias table (app/pc/app_aliases.py) first; an
                unknown name gets a question back, never a guess.
    windows     list / focus / minimize / maximize / restore / close windows
    processes   list / end processes

Risk per call comes from app/security/classifier.py: starting Discord is
`control`, starting PowerShell or regedit, an unknown URI scheme or a
downloaded program is `critical`; ending a system/security process or
Cypher itself is `critical`.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

import psutil
from pydantic import Field

from app.pc import app_aliases, app_index
from app.pc import windows as win
from app.security.classifier import (
    CONTROL,
    CRITICAL,
    SAFE,
    WRITE,
    Verdict,
    classify_executable,
    classify_open_target,
    classify_process,
    classify_program_name,
    classify_start_menu_app,
    verdict,
    worst,
)
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

MAX_PROCESS_LIST = 25
# "youtube.com", "www.orf.at/news" - but not "notepad.exe" or "bericht.pdf".
_DOMAIN_RE = re.compile(r"^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(/\S*)?$", re.IGNORECASE)
_FILE_EXTENSIONS = {"exe", "lnk", "txt", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "png", "jpg", "jpeg", "gif", "mp3", "mp4",
                    "zip", "msi", "bat", "cmd", "ps1", "py", "js", "json", "md", "csv", "html", "htm", "log", "ini", "cfg"}


def _windows_only(cls, settings: Any = None) -> bool:
    return sys.platform == "win32"


# ------------------------------------------------------------------ open_app
@dataclass
class Target:
    launch: str = ""
    label: str = ""
    kind: str = ""  # "url" | "uri" | "path" | "app"
    verdict: Optional[Verdict] = None
    error: str = ""
    clarify: bool = False  # the error is a question to the user ("Welche App meinst du ...?")


def resolve_target(name: str) -> Target:
    text = name.strip().strip('"')
    if not text:
        return Target(error="Was soll ich öffnen?")
    scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):", text)
    if scheme and len(scheme.group(1)) > 1:
        kind = "url" if scheme.group(1).lower() in ("http", "https") else "uri"
        return Target(launch=text, label=text, kind=kind, verdict=classify_open_target(text))
    if re.search(r"[\\/]", text) or re.match(r"^[a-zA-Z]:", text) or text.startswith("~"):
        path = os.path.expandvars(os.path.expanduser(text))
        if not os.path.exists(path):
            return Target(error=f"'{text}' gibt es nicht.", verdict=classify_open_target(text))
        return Target(launch=path, label=path, kind="path", verdict=classify_open_target(path))
    suffix = text.rsplit(".", 1)[-1].lower() if "." in text else ""
    if _DOMAIN_RE.match(text) and suffix not in _FILE_EXTENSIONS:
        url = "https://" + text
        return Target(launch=url, label=url, kind="url", verdict=classify_open_target(url))
    alias = app_aliases.lookup(text)
    if alias is not None:
        return _alias_target(alias)
    match = app_index.find(text)
    if match.entry is not None:
        return _app_target(match.entry)
    if match.candidates:
        return Target(error=f"'{text}' ist nicht eindeutig. Meinst du: {', '.join(match.candidates)}?", verdict=verdict(SAFE), clarify=True)
    exe = shutil.which(text)
    if exe:
        return Target(launch=exe, label=os.path.basename(exe), kind="app", verdict=classify_program_name(exe))
    return Target(error=f"Welche App meinst du mit '{text}'? Im Startmenü gibt es keine App mit diesem Namen.", verdict=verdict(SAFE), clarify=True)


def _app_target(entry: app_index.AppEntry) -> Target:
    return Target(launch=entry.launch, label=entry.name, kind="app", verdict=classify_start_menu_app(entry.target, entry.name))


def _alias_target(alias: app_aliases.Alias) -> Target:
    target = alias.target
    if target == app_aliases.DEFAULT_BROWSER:
        exe = app_aliases.default_browser()
        if not exe:
            return Target(error="Ich finde keinen Standardbrowser. In den Windows-Einstellungen unter Apps > Standard-Apps einen festlegen.", verdict=verdict(SAFE))
        return Target(launch=exe, label=alias.label, kind="app", verdict=classify_executable(exe))
    if target.lower().startswith(app_aliases.APP_PREFIX):
        name = target[len(app_aliases.APP_PREFIX):].strip()
        entry = app_index.exact(name)
        if entry is None:
            return Target(error=f"'{alias.word}' steht für die App '{name}', die gibt es im Startmenü aber nicht.", verdict=verdict(SAFE))
        return _app_target(entry)
    # .exe name, URI or URL
    alias_verdict = classify_open_target(target) if ":" in target else classify_program_name(target)
    return Target(launch=target, label=alias.label, kind="app", verdict=alias_verdict)


class OpenAppArgs(ArgsModel):
    name: str = Field(..., min_length=1, max_length=500, description="App-Name so, wie der Nutzer ihn sagt (z. B. 'Editor', 'Rechner', 'Discord'), Webseite, URI wie 'ms-settings:' oder Datei-/Ordnerpfad")


@register_tool
class OpenAppTool(Tool):
    name = "open_app"
    description = "Eine App, Webseite (im Standardbrowser), URI oder Datei/Ordner öffnen."
    Args = OpenAppArgs
    risk = RiskTier.CONTROL
    confirm_writes = False
    exposed_by_default = True

    is_available = classmethod(_windows_only)

    def classify(self, args: Dict[str, Any], ctx=None):
        target = resolve_target(args.get("name", ""))
        return target.verdict or verdict(SAFE)

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Öffnen: {args.get('name', '')}"

    async def execute(self, db=None, name: str = "", **_) -> ToolResult:
        target = await asyncio.to_thread(resolve_target, name)
        if target.error:
            return ToolResult(success=False, data={"needs_clarification": True} if target.clarify else None, error=target.error)
        try:
            await asyncio.to_thread(os.startfile, target.launch)  # type: ignore[attr-defined]
        except (OSError, AttributeError) as exc:
            return ToolResult(success=False, error=f"'{target.label}' konnte nicht geöffnet werden: {exc}")
        return ToolResult(success=True, data={"opened": target.label, "kind": target.kind})


# ------------------------------------------------------------------ windows
class WindowsArgs(ArgsModel):
    action: Literal["list", "focus", "minimize", "maximize", "restore", "close"] = Field("list", description="minimize ohne title = alle")
    title: Optional[str] = Field(None, description="Teil des Fenstertitels oder Programmname")


def _close_verdict(window: win.Window) -> Verdict:
    owner = classify_process(window.process or "?", window.pid)
    # A folder window belongs to explorer.exe - closing it is harmless.
    if owner.level == CRITICAL and not (window.process.lower() == "explorer.exe" and window.class_name == "CabinetWClass"):
        return owner
    return verdict(WRITE, "ungespeicherte Änderungen: die App fragt normalerweise selbst nach")


@register_tool
class WindowsTool(Tool):
    name = "windows"
    description = "Offene Fenster auflisten oder eines in den Vordergrund holen, minimieren, maximieren, wiederherstellen oder schließen."
    Args = WindowsArgs
    risk = RiskTier.CONTROL
    confirm_writes = False
    exposed_by_default = True

    is_available = classmethod(_windows_only)

    def classify(self, args: Dict[str, Any], ctx=None):
        action = args.get("action", "list")
        if action == "list":
            return verdict(SAFE)
        if action != "close":
            return verdict(CONTROL)
        found = win.find(args.get("title") or "")
        if found.window is None:
            return verdict(WRITE)
        return _close_verdict(found.window)

    def describe(self, args: Dict[str, Any]) -> str:
        labels = {"list": "Fenster auflisten", "focus": "Fenster in den Vordergrund", "minimize": "Fenster minimieren", "maximize": "Fenster maximieren",
                  "restore": "Fenster wiederherstellen", "close": "Fenster schließen"}
        title = args.get("title")
        return labels.get(args.get("action", "list"), "Fenster") + (f": {title}" if title else "")

    async def execute(self, db=None, action: str = "list", title: Optional[str] = None, **_) -> ToolResult:
        if action == "list":
            items = await asyncio.to_thread(win.list_windows)
            return ToolResult(success=True, data={"windows": [{"title": w.title[:80], "app": w.process, "minimized": w.minimized} for w in items[:40]]})
        if action == "minimize" and not (title or "").strip():
            items = await asyncio.to_thread(win.list_windows)
            for window in items:
                await asyncio.to_thread(win.apply, window, "minimize")
            return ToolResult(success=True, data={"minimized": len(items)})
        if not (title or "").strip():
            return ToolResult(success=False, error="Welches Fenster? Bitte einen Teil des Titels oder den Programmnamen angeben.")
        found = await asyncio.to_thread(win.find, title or "")
        if found.window is None:
            if found.candidates:
                return ToolResult(success=False, error=f"Mehrere Fenster passen: {'; '.join(found.candidates)}. Bitte genauer.")
            return ToolResult(success=False, error=f"Kein offenes Fenster passt zu '{title}'.")
        ok = await asyncio.to_thread(win.apply, found.window, action)
        if not ok:
            return ToolResult(success=False, error=f"Windows hat '{action}' für '{found.window.title}' nicht zugelassen.")
        return ToolResult(success=True, data={"window": found.window.title[:80], "app": found.window.process, "action": action})


# ------------------------------------------------------------------ processes
class ProcessesArgs(ArgsModel):
    action: Literal["list", "end"] = "list"
    name: Optional[str] = Field(None, description="Prozessname, z. B. chrome")
    pid: Optional[int] = Field(None, ge=0)


def _cypher_pids() -> set[int]:
    """Cypher itself and the processes that started it (run.py, a launcher)."""
    pids = {os.getpid()}
    try:
        pids.update(p.pid for p in psutil.Process().parents())
    except psutil.Error:
        pass
    return pids


def _matching(name: Optional[str], pid: Optional[int]) -> List[psutil.Process]:
    if pid is not None:
        try:
            return [psutil.Process(pid)]
        except psutil.Error:
            return []
    wanted = (name or "").strip().lower().removesuffix(".exe")
    if not wanted:
        return []
    out = []
    for proc in psutil.process_iter(["name"]):
        if (proc.info["name"] or "").lower().removesuffix(".exe") == wanted:
            out.append(proc)
    return out


@register_tool
class ProcessesTool(Tool):
    name = "processes"
    description = "Laufende Prozesse auflisten (nach Speicher sortiert) oder einen Prozess beenden."
    Args = ProcessesArgs
    risk = RiskTier.SAFE
    confirm_writes = False
    exposed_by_default = True

    def classify(self, args: Dict[str, Any], ctx=None):
        if args.get("action", "list") == "list":
            return verdict(SAFE)
        procs = _matching(args.get("name"), args.get("pid"))
        if not procs:
            return verdict(WRITE)
        own = _cypher_pids()
        verdicts = []
        for proc in procs:
            try:
                proc_name = proc.name()
            except psutil.Error:
                proc_name = args.get("name") or "?"
            if proc.pid in own:
                verdicts.append(verdict(CRITICAL, "das ist Cypher selbst", "cypher_files", "process"))
            else:
                verdicts.append(classify_process(proc_name, proc.pid))
        return worst(verdicts)

    def describe(self, args: Dict[str, Any]) -> str:
        if args.get("action", "list") == "list":
            return "Prozesse auflisten" + (f": {args['name']}" if args.get("name") else "")
        return f"Prozess beenden: {args.get('name') or ''}{' (PID ' + str(args['pid']) + ')' if args.get('pid') is not None else ''}"

    async def execute(self, db=None, action: str = "list", name: Optional[str] = None, pid: Optional[int] = None, **_) -> ToolResult:
        if action == "list":
            return ToolResult(success=True, data={"processes": await asyncio.to_thread(_list, name)})
        if not name and pid is None:
            return ToolResult(success=False, error="Welcher Prozess? Name oder PID angeben.")
        procs = await asyncio.to_thread(_matching, name, pid)
        if not procs:
            return ToolResult(success=False, error=f"Kein laufender Prozess '{name or pid}'.")
        own = _cypher_pids()
        ended, denied, skipped = [], [], []
        for proc in procs:
            if proc.pid in own:
                skipped.append(proc.pid)
                continue
            try:
                proc.terminate()
                ended.append(proc)
            except psutil.AccessDenied:
                denied.append(proc.pid)
            except psutil.NoSuchProcess:
                continue
        await asyncio.to_thread(psutil.wait_procs, ended, 3)
        data: Dict[str, Any] = {"ended": len(ended), "pids": [p.pid for p in ended]}
        if skipped:
            data["skipped_self"] = skipped
        if denied and not ended:
            return ToolResult(success=False, data=data, error="Zugriff verweigert - der Prozess läuft mit Administratorrechten. Cypher hat keine Adminrechte; beenden geht nur über den Task-Manager als Administrator.")
        if skipped and not ended:
            return ToolResult(success=False, data=data, error="Cypher beendet sich nicht selbst. Zum Beenden stop_cypher.bat ausführen.")
        return ToolResult(success=True, data=data)


def _list(name: Optional[str]) -> List[Dict[str, Any]]:
    wanted = (name or "").strip().lower().removesuffix(".exe")
    rows = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        proc_name = proc.info["name"] or ""
        if wanted and wanted not in proc_name.lower():
            continue
        mem = proc.info["memory_info"]
        rows.append({"name": proc_name, "pid": proc.info["pid"], "mb": round((mem.rss if mem else 0) / 1e6)})
    rows.sort(key=lambda r: r["mb"], reverse=True)
    return rows[:MAX_PROCESS_LIST]
