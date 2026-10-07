"""
File tools. Every path goes through the sandbox (app/security/paths.py):
only ALLOWED_DIRECTORIES, never system folders, AppData, key folders,
secret files or Cypher's own data.

Risk per call:
    list/read/search          read_only (read_file needs approval while the
                              cloud provider is active - content would leave the PC)
    write new file / copy     write        -> approval
    overwrite / move / delete destructive  -> approval + warning
Reads are size-limited before reading (no whole-file read of a 5 GB log),
binary files are refused, file content is untrusted.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any, Dict

from pydantic import Field

from app.config import get_settings
from app.security.paths import PathNotAllowedError, allowed_roots, resolve_allowed_path
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

MAX_LIST_ENTRIES = 500
# Kept for older imports; the effective limit is FILE_READ_MAX_BYTES.
MAX_READ_BYTES = 200_000
_BINARY_SNIFF_BYTES = 8192


class _FileToolBase(Tool):
    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return bool(getattr(settings, "allowed_directories_list", []))


def _sandbox(path: str):
    try:
        return resolve_allowed_path(path), None
    except PathNotAllowedError as exc:
        return None, ToolResult(success=False, error=str(exc))


def _exists_quietly(path: str) -> bool:
    try:
        return resolve_allowed_path(path).exists()
    except PathNotAllowedError:
        return False


# ------------------------------------------------------------------ read-only
class PathArgs(ArgsModel):
    path: str = Field(..., description="Pfad relativ zum freigegebenen Ordner oder absolut")


class ListPathArgs(ArgsModel):
    path: str = Field(".", description="Ordner, relativ zum freigegebenen Ordner oder absolut")


@register_tool
class ListFilesTool(_FileToolBase):
    name = "list_files"
    description = "Dateien und Unterordner eines freigegebenen Ordners auflisten."
    Args = ListPathArgs
    risk = RiskTier.READ_ONLY
    untrusted_output = True
    exposed_by_default = True

    async def execute(self, db=None, path: str = ".", **_) -> ToolResult:
        target, err = _sandbox(path)
        if err:
            return err
        if not target.exists():
            return ToolResult(success=False, error=f"'{target.name}' existiert nicht.")
        if not target.is_dir():
            return ToolResult(success=False, error=f"'{target.name}' ist kein Ordner.")

        def _list():
            entries = []
            for child in sorted(target.iterdir()):
                if len(entries) >= MAX_LIST_ENTRIES:
                    break
                entries.append({"name": child.name, "is_dir": child.is_dir(), "size": child.stat().st_size if child.is_file() else None})
            return entries

        return ToolResult(success=True, data={"path": str(target), "entries": await asyncio.to_thread(_list)})


@register_tool
class ReadFileTool(_FileToolBase):
    name = "read_file"
    description = "Den Textinhalt einer Datei in einem freigegebenen Ordner lesen."
    Args = PathArgs
    risk = RiskTier.READ_ONLY
    untrusted_output = True
    exposed_by_default = True

    def risk_for(self, args: Dict[str, Any], ctx=None) -> RiskTier:
        if ctx is not None and ctx.provider == "cloud":
            return RiskTier.WRITE
        return self.base_risk()

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Datei lesen: {args.get('path', '')}"

    async def execute(self, db=None, path: str = "", **_) -> ToolResult:
        target, err = _sandbox(path)
        if err:
            return err
        if not target.is_file():
            return ToolResult(success=False, error=f"'{target.name}' ist keine Datei oder existiert nicht.")
        limit = get_settings().FILE_READ_MAX_BYTES

        def _read():
            size = target.stat().st_size
            with target.open("rb") as fh:
                raw = fh.read(limit)
            return size, raw

        try:
            size, raw = await asyncio.to_thread(_read)
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
            return ToolResult(success=False, error=f"'{target.name}' ist eine Binärdatei und kann nicht als Text gelesen werden.")
        return ToolResult(success=True, data={"path": str(target), "content": raw.decode("utf-8", errors="replace"), "truncated": size > limit, "size_bytes": size})


class SearchFilesArgs(ArgsModel):
    query: str = Field(..., min_length=1, description="Teil des Dateinamens")
    path: str = Field(".", description="Ordner, in dem gesucht wird")


@register_tool
class SearchFilesTool(_FileToolBase):
    name = "search_files"
    description = "Dateien in freigegebenen Ordnern nach Namen suchen (Groß-/Kleinschreibung egal)."
    Args = SearchFilesArgs
    risk = RiskTier.READ_ONLY
    untrusted_output = True

    async def execute(self, db=None, query: str = "", path: str = ".", **_) -> ToolResult:
        root, err = _sandbox(path)
        if err:
            return err
        if not root.exists():
            return ToolResult(success=False, error=f"'{root.name}' existiert nicht.")

        def _search():
            query_lower = query.lower()
            matches: list[str] = []
            for entry in root.rglob("*"):
                if query_lower in entry.name.lower():
                    matches.append(str(entry))
                    if len(matches) >= MAX_LIST_ENTRIES:
                        break
            return matches

        return ToolResult(success=True, data={"query": query, "matches": await asyncio.to_thread(_search)})


# ------------------------------------------------------------------ write
class WriteFileArgs(ArgsModel):
    path: str = Field(..., description="Zieldatei")
    content: str = Field(..., description="Neuer Textinhalt")
    overwrite: bool = Field(False, description="true = bestehende Datei ersetzen")


@register_tool
class WriteFileTool(_FileToolBase):
    name = "write_file"
    description = "Eine Textdatei in einem freigegebenen Ordner anlegen (oder mit overwrite=true ersetzen). Braucht Bestätigung."
    Args = WriteFileArgs
    risk = RiskTier.WRITE
    exposed_by_default = True

    def risk_for(self, args: Dict[str, Any], ctx=None) -> RiskTier:
        return RiskTier.DESTRUCTIVE if args.get("overwrite") or _exists_quietly(args.get("path", "")) else RiskTier.WRITE

    def describe(self, args: Dict[str, Any]) -> str:
        content = args.get("content", "")
        verb = "überschreiben" if args.get("overwrite") or _exists_quietly(args.get("path", "")) else "anlegen"
        return f"Datei {args.get('path', '')} {verb} ({len(content)} Zeichen)"

    async def execute(self, db=None, path: str = "", content: str = "", overwrite: bool = False, **_) -> ToolResult:
        limit = get_settings().FILE_WRITE_MAX_BYTES
        encoded = content.encode("utf-8")
        if len(encoded) > limit:
            return ToolResult(success=False, error=f"Inhalt ist zu groß ({len(encoded)} Bytes, Limit {limit}).")
        target, err = _sandbox(path)
        if err:
            return err
        if target.exists() and target.is_dir():
            return ToolResult(success=False, error=f"'{target.name}' ist ein Ordner.")

        def _write():
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temp file first so a crash never leaves half a file.
            tmp = target.with_name(f".{target.name}.cypher-tmp")
            tmp.write_bytes(encoded)
            os.replace(tmp, target)

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"path": str(target), "bytes_written": len(encoded)})


class SourceDestArgs(ArgsModel):
    source: str
    destination: str


@register_tool
class MoveFileTool(_FileToolBase):
    name = "move_file"
    description = "Datei oder Ordner innerhalb der freigegebenen Ordner verschieben/umbenennen. Braucht Bestätigung."
    Args = SourceDestArgs
    risk = RiskTier.DESTRUCTIVE

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Verschieben: {args.get('source', '')} → {args.get('destination', '')}"

    async def execute(self, db=None, source: str = "", destination: str = "", **_) -> ToolResult:
        src, err = _sandbox(source)
        if err:
            return err
        dst, err = _sandbox(destination)
        if err:
            return err
        if not src.exists():
            return ToolResult(success=False, error=f"'{src.name}' existiert nicht.")
        if dst.exists():
            return ToolResult(success=False, error=f"Ziel '{dst.name}' existiert bereits - wird nicht überschrieben.")
        try:
            await asyncio.to_thread(lambda: (dst.parent.mkdir(parents=True, exist_ok=True), shutil.move(str(src), str(dst))))
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"source": str(src), "destination": str(dst)})


@register_tool
class CopyFileTool(_FileToolBase):
    name = "copy_file"
    description = "Eine Datei innerhalb der freigegebenen Ordner kopieren. Braucht Bestätigung."
    Args = SourceDestArgs
    risk = RiskTier.WRITE

    def risk_for(self, args: Dict[str, Any], ctx=None) -> RiskTier:
        return RiskTier.DESTRUCTIVE if _exists_quietly(args.get("destination", "")) else RiskTier.WRITE

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Kopieren: {args.get('source', '')} → {args.get('destination', '')}"

    async def execute(self, db=None, source: str = "", destination: str = "", **_) -> ToolResult:
        src, err = _sandbox(source)
        if err:
            return err
        dst, err = _sandbox(destination)
        if err:
            return err
        if not src.is_file():
            return ToolResult(success=False, error=f"'{src.name}' ist keine Datei.")
        try:
            await asyncio.to_thread(lambda: (dst.parent.mkdir(parents=True, exist_ok=True), shutil.copy2(str(src), str(dst))))
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"source": str(src), "destination": str(dst)})


@register_tool
class DeleteFileTool(_FileToolBase):
    name = "delete_file"
    description = "Eine Datei oder einen LEEREN Ordner in den freigegebenen Ordnern endgültig löschen. Braucht Bestätigung."
    Args = PathArgs
    risk = RiskTier.DESTRUCTIVE

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Endgültig löschen: {args.get('path', '')}"

    async def execute(self, db=None, path: str = "", **_) -> ToolResult:
        target, err = _sandbox(path)
        if err:
            return err
        if not target.exists():
            return ToolResult(success=False, error=f"'{target.name}' existiert nicht.")
        if any(target == root for root in allowed_roots()):
            return ToolResult(success=False, error="Ein freigegebener Hauptordner selbst kann nicht gelöscht werden.")
        try:
            # Intentionally never rmtree: one confirmed call must not wipe a tree.
            await asyncio.to_thread(target.rmdir if target.is_dir() else target.unlink)
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"path": str(target), "deleted": True})
