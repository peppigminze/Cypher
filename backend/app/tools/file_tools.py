"""
Files on all local drives (FIX2 point 3) - the ALLOWED_DIRECTORIES sandbox
no longer applies here (it still scopes the git tools).

    list_files   folder contents                         safe
    read_file    text of a file (size-limited, no binary) safe
    write_file   create / overwrite / append a text file  write
    file_op      copy, move, rename, delete, mkdir        write
                 delete = RECYCLE BIN (send2trash), never permanent;
                 overwriting moves the old version to the recycle bin first

Risk per call: app/security/classifier.py classify_path - writes outside
your user profile, system folders, the Startup folder, whole folders,
credentials (.ssh, .env, browser cookies ...) and Cypher's own files are
`critical`; Cypher's token/config/audit log are on the red line.
Relative paths start in your user profile; "Desktop", "Dokumente",
"Downloads" ... mean the real folders (app/pc/known_folders.py).
File names and content are untrusted (prompt-injection defence).
"""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from app.config import get_settings
from app.security.classifier import SAFE, WRITE, classify_path, resolve_path, verdict, worst
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

MAX_LIST_ENTRIES = 300
_BINARY_SNIFF_BYTES = 8192


def _trash(path: str) -> None:
    from send2trash import send2trash  # noqa: PLC0415

    send2trash(path)


# ------------------------------------------------------------------ read
class ListPathArgs(ArgsModel):
    path: str = Field("~", description="Ordner, z. B. Desktop, Downloads oder D:\\Projekte")


@register_tool
class ListFilesTool(Tool):
    name = "list_files"
    description = "Dateien und Unterordner eines Ordners auflisten."
    Args = ListPathArgs
    risk = RiskTier.SAFE
    untrusted_output = True
    confirm_writes = False
    exposed_by_default = True

    def classify(self, args: Dict[str, Any], ctx=None):
        return classify_path(args.get("path") or "~", "list")

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Ordner anzeigen: {args.get('path') or '~'}"

    async def execute(self, db=None, path: str = "~", **_) -> ToolResult:
        target = resolve_path(path or "~")
        if not os.path.isdir(target):
            return ToolResult(success=False, error=f"Den Ordner '{target}' gibt es nicht.")

        def _list():
            entries = []
            with os.scandir(target) as it:
                for entry in sorted(it, key=lambda e: (not e.is_dir(), e.name.lower())):
                    if len(entries) >= MAX_LIST_ENTRIES:
                        break
                    try:
                        size = entry.stat().st_size if entry.is_file() else None
                    except OSError:
                        size = None
                    entries.append({"name": entry.name, "dir": entry.is_dir(), "size": size})
            return entries

        try:
            entries = await asyncio.to_thread(_list)
        except OSError as exc:
            return ToolResult(success=False, error=f"Kein Zugriff auf '{target}': {exc.strerror or exc}")
        return ToolResult(success=True, data={"path": target, "entries": entries, "truncated": len(entries) >= MAX_LIST_ENTRIES})


class PathArgs(ArgsModel):
    path: str = Field(..., description="Dateipfad")


@register_tool
class ReadFileTool(Tool):
    name = "read_file"
    description = "Den Textinhalt einer Datei lesen."
    Args = PathArgs
    risk = RiskTier.SAFE
    untrusted_output = True
    # The only `write` here is "content would go to the cloud model" -> ask.
    confirm_writes = True
    exposed_by_default = True

    def classify(self, args: Dict[str, Any], ctx=None):
        result = classify_path(args.get("path", ""), "read")
        if result.level == SAFE and ctx is not None and ctx.provider == "cloud":
            return verdict(WRITE, "der Inhalt ginge an das Cloud-Modell")
        return result

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Datei lesen: {args.get('path', '')}"

    async def execute(self, db=None, path: str = "", **_) -> ToolResult:
        target = resolve_path(path)
        if not os.path.isfile(target):
            return ToolResult(success=False, error=f"'{target}' ist keine Datei oder existiert nicht.")
        limit = get_settings().FILE_READ_MAX_BYTES

        def _read():
            with open(target, "rb") as fh:
                return os.path.getsize(target), fh.read(limit)

        try:
            size, raw = await asyncio.to_thread(_read)
        except OSError as exc:
            return ToolResult(success=False, error=f"'{target}' kann nicht gelesen werden: {exc.strerror or exc}")
        if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
            return ToolResult(success=False, error=f"'{os.path.basename(target)}' ist keine Textdatei.")
        text = raw.decode("utf-8", errors="replace")
        cap = get_settings().FETCH_MAX_CHARS
        return ToolResult(success=True, data={"path": target, "content": text[:cap], "truncated": size > limit or len(text) > cap, "size_bytes": size})


# ------------------------------------------------------------------ write
class WriteFileArgs(ArgsModel):
    path: str = Field(..., description="Zieldatei")
    content: str = Field(..., description="Textinhalt")
    mode: Literal["create", "overwrite", "append"] = Field("create", description="overwrite: alte Version kommt in den Papierkorb")


@register_tool
class WriteFileTool(Tool):
    name = "write_file"
    description = "Eine Textdatei anlegen, überschreiben oder ergänzen."
    Args = WriteFileArgs
    risk = RiskTier.WRITE
    confirm_writes = False
    exposed_by_default = True

    def classify(self, args: Dict[str, Any], ctx=None):
        return classify_path(args.get("path", ""), "write")

    def describe(self, args: Dict[str, Any]) -> str:
        verb = {"create": "anlegen", "overwrite": "überschreiben (alte Version in den Papierkorb)", "append": "ergänzen"}.get(args.get("mode", "create"), "schreiben")
        return f"Datei {args.get('path', '')} {verb} ({len(args.get('content', ''))} Zeichen)"

    async def execute(self, db=None, path: str = "", content: str = "", mode: str = "create", **_) -> ToolResult:
        limit = get_settings().FILE_WRITE_MAX_BYTES
        encoded = content.encode("utf-8")
        if len(encoded) > limit:
            return ToolResult(success=False, error=f"Inhalt ist zu groß ({len(encoded)} Bytes, Limit {limit}).")
        target = resolve_path(path)
        if os.path.isdir(target):
            return ToolResult(success=False, error=f"'{target}' ist ein Ordner.")
        exists = os.path.exists(target)
        if exists and mode == "create":
            return ToolResult(success=False, error=f"'{os.path.basename(target)}' gibt es schon. mode=overwrite (alte Version in den Papierkorb) oder mode=append verwenden.")

        def _write():
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if mode == "append":
                with open(target, "ab") as fh:
                    fh.write(encoded)
                return
            if exists:
                _trash(target)
            tmp = os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.cypher-tmp")
            with open(tmp, "wb") as fh:
                fh.write(encoded)
            os.replace(tmp, target)  # never half a file

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            return ToolResult(success=False, error=f"Schreiben fehlgeschlagen: {exc.strerror or exc}")
        data: Dict[str, Any] = {"path": target, "bytes": len(encoded), "mode": mode}
        if exists and mode == "overwrite":
            data["old_version"] = "im Papierkorb"
        return ToolResult(success=True, data=data)


class FileOpArgs(ArgsModel):
    action: Literal["copy", "move", "rename", "delete", "mkdir"]
    path: str = Field(..., description="Datei oder Ordner")
    to: Optional[str] = Field(None, description="Ziel (bei rename nur der neue Name)")


def _destination(action: str, path: str, to: Optional[str]) -> Optional[str]:
    if not to:
        return None
    if action == "rename":
        return os.path.join(os.path.dirname(resolve_path(path)), os.path.basename(to.strip().rstrip("\\/")))
    dest = resolve_path(to)
    # "copy x.txt to Desktop" -> into the folder
    return os.path.join(dest, os.path.basename(resolve_path(path))) if os.path.isdir(dest) else dest


@register_tool
class FileOpTool(Tool):
    name = "file_op"
    description = "Datei/Ordner kopieren, verschieben, umbenennen, löschen (kommt in den Papierkorb) oder Ordner anlegen."
    Args = FileOpArgs
    risk = RiskTier.WRITE
    confirm_writes = False
    exposed_by_default = True

    def classify(self, args: Dict[str, Any], ctx=None):
        action, path = args.get("action"), args.get("path", "")
        dest = _destination(action or "", path, args.get("to"))
        if action == "delete":
            return classify_path(path, "delete")
        if action == "mkdir":
            return classify_path(path, "write", is_dir=False)
        if action == "copy":
            return worst([classify_path(path, "read"), classify_path(dest or "", "write", is_dir=False)])
        # move / rename: both places change (moving a folder is not overwriting it)
        return worst([classify_path(path, "write", is_dir=False), classify_path(dest or "", "write", is_dir=False)])

    def describe(self, args: Dict[str, Any]) -> str:
        labels = {"copy": "Kopieren", "move": "Verschieben", "rename": "Umbenennen", "delete": "In den Papierkorb", "mkdir": "Ordner anlegen"}
        to = f" → {args['to']}" if args.get("to") else ""
        return f"{labels.get(args.get('action', ''), 'Datei')}: {args.get('path', '')}{to}"

    async def execute(self, db=None, action: str = "", path: str = "", to: Optional[str] = None, **_) -> ToolResult:
        src = resolve_path(path)
        if action == "mkdir":
            if os.path.exists(src):
                return ToolResult(success=False, error=f"'{src}' gibt es schon.")
            await asyncio.to_thread(os.makedirs, src)
            return ToolResult(success=True, data={"created": src})
        if not os.path.exists(src):
            return ToolResult(success=False, error=f"'{src}' gibt es nicht.")
        if action == "delete":
            try:
                await asyncio.to_thread(_trash, src)
            except OSError as exc:
                return ToolResult(success=False, error=f"Konnte nicht in den Papierkorb verschoben werden: {exc}")
            return ToolResult(success=True, data={"path": src, "recycle_bin": True})
        dest = _destination(action, path, to)
        if not dest:
            return ToolResult(success=False, error="Wohin? 'to' fehlt.")
        if os.path.exists(dest):
            return ToolResult(success=False, error=f"'{dest}' gibt es schon - wird nicht überschrieben.")

        def _do():
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if action == "copy":
                if os.path.isdir(src):
                    shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest)
            else:
                shutil.move(src, dest)

        try:
            await asyncio.to_thread(_do)
        except OSError as exc:
            return ToolResult(success=False, error=f"{action} fehlgeschlagen: {exc.strerror or exc}")
        return ToolResult(success=True, data={"from": src, "to": dest, "action": action})
