"""
File system tools (project spec section 13). Every path goes through
resolve_allowed_path() (app/tools/paths.py) - nothing outside
ALLOWED_DIRECTORIES is reachable. Read-only tools are SAFE; anything
that writes, moves, copies, or deletes is CONFIRM_REQUIRED (project
spec section 14).
"""
from __future__ import annotations

import shutil

from sqlalchemy.orm import Session

from app.tools.base import Tool, ToolResult, ToolSecurity
from app.tools.paths import PathNotAllowedError, resolve_allowed_path

MAX_READ_BYTES = 200_000  # don't dump huge files into the LLM's context
MAX_LIST_ENTRIES = 500


class ListFilesTool(Tool):
    name = "list_files"
    description = "List files and folders in a directory Cypher is allowed to access."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, path: str = ".", **_) -> ToolResult:
        try:
            target = resolve_allowed_path(path)
        except PathNotAllowedError as exc:
            return ToolResult(success=False, error=str(exc))
        if not target.exists():
            return ToolResult(success=False, error=f"Path '{target}' does not exist.")
        if not target.is_dir():
            return ToolResult(success=False, error=f"Path '{target}' is not a directory.")

        entries = []
        for child in sorted(target.iterdir()):
            if len(entries) >= MAX_LIST_ENTRIES:
                break
            entries.append(
                {
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return ToolResult(success=True, data={"path": str(target), "entries": entries})


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the text content of a file Cypher is allowed to access."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, path: str = "", **_) -> ToolResult:
        try:
            target = resolve_allowed_path(path)
        except PathNotAllowedError as exc:
            return ToolResult(success=False, error=str(exc))
        if not target.is_file():
            return ToolResult(success=False, error=f"'{target}' is not a file.")
        try:
            size = target.stat().st_size
            content = target.read_bytes()[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(
            success=True,
            data={"path": str(target), "content": content, "truncated": size > MAX_READ_BYTES},
        )


class SearchFilesTool(Tool):
    name = "search_files"
    description = "Search for files by filename (case-insensitive substring) within allowed directories."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "path": {"type": "string"}},
        "required": ["query"],
    }
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, query: str = "", path: str = ".", **_) -> ToolResult:
        try:
            root = resolve_allowed_path(path)
        except PathNotAllowedError as exc:
            return ToolResult(success=False, error=str(exc))
        if not root.exists():
            return ToolResult(success=False, error=f"Path '{root}' does not exist.")

        query_lower = query.lower()
        matches: list[str] = []
        for entry in root.rglob("*"):
            if query_lower in entry.name.lower():
                matches.append(str(entry))
                if len(matches) >= MAX_LIST_ENTRIES:
                    break
        return ToolResult(success=True, data={"query": query, "matches": matches})


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a text file with the given content. Requires confirmation."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }
    security = ToolSecurity.CONFIRM_REQUIRED

    async def execute(self, db: Session | None = None, path: str = "", content: str = "", **_) -> ToolResult:
        try:
            target = resolve_allowed_path(path)
        except PathNotAllowedError as exc:
            return ToolResult(success=False, error=str(exc))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"path": str(target), "bytes_written": len(content.encode("utf-8"))})


class MoveFileTool(Tool):
    name = "move_file"
    description = "Move or rename a file/folder within allowed directories. Requires confirmation."
    parameters = {
        "type": "object",
        "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
        "required": ["source", "destination"],
    }
    security = ToolSecurity.CONFIRM_REQUIRED

    async def execute(self, db: Session | None = None, source: str = "", destination: str = "", **_) -> ToolResult:
        try:
            src = resolve_allowed_path(source)
            dst = resolve_allowed_path(destination)
        except PathNotAllowedError as exc:
            return ToolResult(success=False, error=str(exc))
        if not src.exists():
            return ToolResult(success=False, error=f"Source '{src}' does not exist.")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"source": str(src), "destination": str(dst)})


class CopyFileTool(Tool):
    name = "copy_file"
    description = "Copy a file within allowed directories. Requires confirmation."
    parameters = {
        "type": "object",
        "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
        "required": ["source", "destination"],
    }
    security = ToolSecurity.CONFIRM_REQUIRED

    async def execute(self, db: Session | None = None, source: str = "", destination: str = "", **_) -> ToolResult:
        try:
            src = resolve_allowed_path(source)
            dst = resolve_allowed_path(destination)
        except PathNotAllowedError as exc:
            return ToolResult(success=False, error=str(exc))
        if not src.is_file():
            return ToolResult(success=False, error=f"Source '{src}' is not a file.")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"source": str(src), "destination": str(dst)})


class DeleteFileTool(Tool):
    name = "delete_file"
    description = (
        "Permanently delete a file or empty folder within allowed directories. "
        "Requires confirmation. Refuses to delete a non-empty folder."
    )
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    security = ToolSecurity.CONFIRM_REQUIRED

    async def execute(self, db: Session | None = None, path: str = "", **_) -> ToolResult:
        try:
            target = resolve_allowed_path(path)
        except PathNotAllowedError as exc:
            return ToolResult(success=False, error=str(exc))
        if not target.exists():
            return ToolResult(success=False, error=f"Path '{target}' does not exist.")
        try:
            if target.is_dir():
                # Intentionally NOT shutil.rmtree: a single confirmed tool
                # call must never be able to wipe an entire directory tree.
                target.rmdir()
            else:
                target.unlink()
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"path": str(target), "deleted": True})
