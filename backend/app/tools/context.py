"""Per-call context handed to the executor and to tools."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import Settings, get_settings

# "Merk dir ...", "Merke dir ...", "Speicher dir ...", "Vergiss nicht, dass ..."
_EXPLICIT_MEMORY_RE = re.compile(r"\b(merk(e)?\s+dir|speicher(e)?\s+dir|vergiss\s+nicht|behalte?\s+im\s+kopf|remember\s+that)\b", re.IGNORECASE)
_EXPLICIT_FORGET_RE = re.compile(r"\b(vergiss|lösch(e)?\s+.*(erinnerung|gemerkt)|forget)\b", re.IGNORECASE)


@dataclass
class ToolContext:
    db: Any = None
    settings: Settings = field(default_factory=get_settings)
    # Which LLM produced the call: "local" or "cloud". Some tools ask for
    # approval only when data would leave the PC (cloud).
    provider: str = "local"
    # "voice" | "text" | "test" - for the audit log and response length.
    source: str = "text"
    conversation_id: Optional[str] = None
    # The latest user utterance of this turn (for explicit-intent rules).
    user_message: str = ""
    # True once untrusted content (web page, file, clipboard, screen) was
    # added to this turn. Tools with allow_when_tainted=False refuse then.
    tainted: bool = False

    @property
    def explicit_memory_request(self) -> bool:
        return bool(_EXPLICIT_MEMORY_RE.search(self.user_message or ""))

    @property
    def explicit_forget_request(self) -> bool:
        return bool(_EXPLICIT_FORGET_RE.search(self.user_message or ""))
