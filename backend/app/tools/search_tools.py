"""
Local file search (Phase 6) - find_files.

Deliberately a distinct tool/name from app/tools/file_tools.py's
SearchFilesTool: that one is scoped to ALLOWED_DIRECTORIES (the same
read/write sandbox as read_file/write_file, empty and opt-in by
default) and both tools would otherwise collide on the "search_files"
name in the shared registry (app/tools/defaults.py - ToolRegistry
rejects registering two tools under the same name). find_files instead
searches its own configured SEARCH_DIRECTORIES (app/config.py) and
never touches file content, only path/filename/modified-date - lower
risk, so it defaults to something useful (the user's home directory)
instead of being opt-in-only.

Windows Search Index integration (COM via pywin32) was considered per
the Phase 6 spec ("nutze den vorhandenen Windows-Suchindex falls
sinnvoll zugänglich") but deliberately skipped: it would add a heavy,
somewhat fragile dependency (COM registration, provider quirks) for a
tool that a plain, capped directory walk already serves robustly - see
the "einfach, robust" framing of the Phase 6 request. The walk below
prunes common huge/irrelevant directories and hard-caps how many files
it will even look at, so a large home directory can't turn one search
into a multi-minute scan.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.tools.base import Tool, ToolResult, ToolSecurity

# Hard cap on how many files the walk will even inspect, independent of
# how many actually match - bounds worst-case latency on a huge home
# directory even for a query that matches nothing.
MAX_SCANNED_FILES = 50_000

# Directories that are either huge, irrelevant to a filename search, or
# both - pruned in place so os.walk never descends into them at all.
_SKIP_DIR_NAMES = {
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "AppData",
    "$Recycle.Bin",
    "System Volume Information",
}


class FindFilesTool(Tool):
    name = "find_files"
    description = (
        "Search for files by filename (case-insensitive substring match) across the "
        "configured search directories (SEARCH_DIRECTORIES in .env, defaults to the "
        "user's home directory). Read-only: returns path/filename/last-modified only, "
        "never file content."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
    }
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, query: str = "", max_results: int = 10, **_) -> ToolResult:
        query = query.strip()
        if not query:
            return ToolResult(success=False, error="'query' must not be empty.")

        settings = get_settings()
        try:
            limit = max(1, min(int(max_results), settings.SEARCH_MAX_RESULTS))
        except (TypeError, ValueError):
            return ToolResult(success=False, error=f"'max_results' must be a number, got {max_results!r}.")

        roots = [Path(d) for d in settings.search_directories_list]

        try:
            matches, warnings = await asyncio.to_thread(self._walk, roots, query.lower(), limit)
        except Exception as exc:  # noqa: BLE001 - walking arbitrary user directories, be defensive
            return ToolResult(success=False, error=f"File search failed unexpectedly: {exc}")

        return ToolResult(
            success=True,
            data={"query": query, "count": len(matches), "results": matches, "warnings": warnings or None},
        )

    @staticmethod
    def _walk(roots: list[Path], query_lower: str, limit: int) -> tuple[list[dict], list[str]]:
        matches: list[dict] = []
        warnings: list[str] = []
        scanned = 0

        for root in roots:
            if not root.exists():
                warnings.append(f"'{root}' does not exist - skipped.")
                continue

            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".")]

                for filename in filenames:
                    scanned += 1
                    if scanned > MAX_SCANNED_FILES:
                        warnings.append(f"Stopped after scanning {MAX_SCANNED_FILES} files - results may be incomplete.")
                        return matches, warnings
                    if len(matches) >= limit:
                        return matches, warnings
                    if query_lower not in filename.lower():
                        continue

                    full_path = Path(dirpath) / filename
                    try:
                        modified = datetime.fromtimestamp(full_path.stat().st_mtime, tz=timezone.utc).isoformat()
                    except OSError:
                        modified = None
                    matches.append({"path": str(full_path), "filename": filename, "modified": modified})

        return matches, warnings
