"""
Notes: "Schreib eine Notiz: Milch und Brot kaufen".

Notes are Markdown files in NOTES_DIR (default data/notes), one file per
day, entries appended with a timestamp - readable with any editor and
included in backups. Writing is a WRITE-tier action: the approval card
shows the exact text before anything is saved (acceptance test 4).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Field

from app.config import get_settings
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool
from app.tools.system_tools import local_now

MAX_NOTE_CHARS = 5000


def _append_note(notes_dir: Path, text: str, title: Optional[str]) -> Dict[str, Any]:
    now = local_now()
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"{now:%Y-%m-%d}.md"
    heading = f"## {now:%H:%M}" + (f" – {title.strip()}" if title and title.strip() else "")
    entry = f"{heading}\n\n{text.strip()}\n\n"
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if is_new:
            fh.write(f"# Notizen vom {now:%d.%m.%Y}\n\n")
        fh.write(entry)
    return {"file": path.name, "saved_at": now.strftime("%H:%M"), "chars": len(text)}


def _list_notes(notes_dir: Path, limit: int) -> list[Dict[str, Any]]:
    if not notes_dir.exists():
        return []
    entries = []
    for path in sorted(notes_dir.glob("*.md"), reverse=True)[:7]:
        content = path.read_text(encoding="utf-8")
        for block in re.split(r"\n(?=## )", content):
            if block.startswith("## "):
                head, _, body = block.partition("\n")
                entries.append({"day": path.stem, "heading": head[3:].strip(), "text": body.strip()[:500]})
    return entries[:limit]


class SaveNoteArgs(ArgsModel):
    text: str = Field(..., min_length=1, max_length=MAX_NOTE_CHARS, description="Inhalt der Notiz")
    title: Optional[str] = Field(None, max_length=100, description="Optionaler kurzer Titel")


@register_tool
class SaveNoteTool(Tool):
    name = "save_note"
    description = "Eine Notiz speichern (Text, optional mit Titel). Für 'schreib dir auf', 'notiere', 'schreib eine Notiz'."
    Args = SaveNoteArgs
    risk = RiskTier.WRITE
    exposed_by_default = True

    async def execute(self, db=None, text: str = "", title: Optional[str] = None, **_) -> ToolResult:
        try:
            data = await asyncio.to_thread(_append_note, get_settings().notes_dir_path, text, title)
        except OSError as exc:
            return ToolResult(success=False, error=f"Notiz konnte nicht gespeichert werden: {exc}")
        return ToolResult(success=True, data=data)

    def describe(self, args: Dict[str, Any]) -> str:
        text = args.get("text", "")
        title = f" „{args['title']}“" if args.get("title") else ""
        return f"Notiz{title} speichern: „{text[:120]}{'…' if len(text) > 120 else ''}“"


class ListNotesArgs(ArgsModel):
    limit: int = Field(5, ge=1, le=20)


@register_tool
class ListNotesTool(Tool):
    name = "list_notes"
    description = "Die letzten gespeicherten Notizen vorlesen/anzeigen."
    Args = ListNotesArgs
    risk = RiskTier.READ_ONLY
    exposed_by_default = True

    async def execute(self, db=None, limit: int = 5, **_) -> ToolResult:
        notes = await asyncio.to_thread(_list_notes, get_settings().notes_dir_path, limit)
        return ToolResult(success=True, data={"count": len(notes), "notes": notes})
