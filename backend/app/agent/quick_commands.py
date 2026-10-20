"""
Quick commands: one- or two-word media/volume commands ("Pause.", "Leiser.",
"Nächster Titel.") run directly, without asking the LLM.

Why: these are the most frequent voice commands, they are unambiguous for
the user but not for the model ("Pause." alone got answered with "Alles
klar." in 2/2 runs - no context), and skipping the LLM makes them instant
(~0.1 s instead of ~1 s). Only an EXACT match of the whole utterance counts
(after removing politeness words and the wake phrase), so "Mach eine Pause
und erklär mir..." still goes to the LLM.

The call still goes through the ToolExecutor (validation, risk tier, audit
log) - quick commands are a shortcut for the decision, not for security.

"Open X" (FIX2): "Mach mir den Editor auf", "Öffne den Rechner", "Spotify
starten". When the whole utterance is such a command and X is an alias
(app/pc/app_aliases.py) or the exact name of an installed app, open_app
runs with the user's own word - the model never gets to reinterpret
"Editor" as VS Code. Anything else (unknown word, website, file, longer
sentence) goes to the LLM as before; open_app answers an unknown app name
with a question, not a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.pc import app_aliases, app_index


@dataclass(frozen=True)
class QuickCommand:
    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    reply: str = ""


_PAUSE = QuickCommand("media_control", {"action": "pause"}, "Pausiert.")
_PLAY = QuickCommand("media_control", {"action": "play"}, "Läuft weiter.")
_NEXT = QuickCommand("media_control", {"action": "next"}, "Nächster Titel.")
_PREVIOUS = QuickCommand("media_control", {"action": "previous"}, "Vorheriger Titel.")
_QUIETER = QuickCommand("set_volume", {"direction": "down"}, "Leiser.")
_LOUDER = QuickCommand("set_volume", {"direction": "up"}, "Lauter.")
_MUTE = QuickCommand("set_volume", {"mute": True}, "Stumm.")
_UNMUTE = QuickCommand("set_volume", {"mute": False}, "Ton ist wieder an.")

_COMMANDS: Dict[str, QuickCommand] = {}


def _add(command: QuickCommand, *phrases: str) -> None:
    for phrase in phrases:
        _COMMANDS[phrase] = command


_add(_PAUSE, "pause", "pausieren", "pausier", "pausiere", "musik pause", "musik pausieren", "pausier die musik",
     "stopp", "stop", "musik stopp", "musik stop", "stopp die musik", "halt die musik an", "musik aus")
_add(_PLAY, "weiter", "play", "spiel weiter", "weiterspielen", "musik weiter", "musik an", "fortsetzen",
     "wiedergabe fortsetzen", "mach die musik wieder an")
_add(_NEXT, "nächster titel", "nächster song", "nächstes lied", "nächster track", "nächstes", "skip",
     "überspringen", "lied überspringen", "song überspringen")
_add(_PREVIOUS, "vorheriger titel", "vorheriger song", "vorheriges lied", "letztes lied", "letzter song")
_add(_QUIETER, "leiser", "mach leiser", "etwas leiser", "musik leiser", "mach die musik leiser", "lautstärke runter")
_add(_LOUDER, "lauter", "mach lauter", "etwas lauter", "musik lauter", "mach die musik lauter", "lautstärke hoch")
_add(_MUTE, "stumm", "ton aus", "stummschalten", "mute", "lautlos")
_add(_UNMUTE, "ton an", "ton wieder an", "stumm aus", "unmute")

# Words that don't change the meaning of a short command.
_FILLER = re.compile(r"\b(bitte|mal|kurz|doch|jetzt|danke|hey|cypher|okay|ok)\b")
_PUNCT = re.compile(r"[^\wäöüß ]+")


def normalize(text: str) -> str:
    text = _PUNCT.sub(" ", text.lower())
    text = _FILLER.sub(" ", text)
    return " ".join(text.split())


def match_quick_command(text: str) -> Optional[QuickCommand]:
    """The command for an exact short utterance, else None (-> LLM)."""
    if len(text) > 60:
        return None
    return _COMMANDS.get(normalize(text))


# ------------------------------------------------------------------ open X
_OPEN_PATTERNS = (
    re.compile(r"^(?:öffne|oeffne|starte|open|launch)\s+(?P<term>.+)$"),
    re.compile(r"^(?:mach|mache)\s+(?P<term>.+?)\s+auf$"),
    re.compile(r"^(?:kannst|könntest|würdest)\s+du\s+(?P<term>.+?)\s+(?:öffnen|oeffnen|starten|aufmachen)$"),
    re.compile(r"^(?P<term>.+?)\s+(?:öffnen|oeffnen|starten|aufmachen)$"),
)
# Not app names: "Starte die Musik", "Starte einen Timer" mean something else.
_NOT_APPS = re.compile(r"\b(musik|song|lied|titel|playlist|wiedergabe|timer|wecker|countdown|stoppuhr|erinnerung)\b")
_OPEN_FILLER = re.compile(r"\b(bitte|mal|kurz|doch|jetzt|schnell|danke|hey|cypher|okay|ok|mir|uns|ein|eine|einen|mein|meine|meinen)\b")


def open_term(text: str) -> Optional[str]:
    """'Mach mir bitte den Editor auf!' -> 'editor'. None if it is not a
    plain open command for one app word (max. four words, no path/URL)."""
    if len(text) > 80:
        return None
    lowered = re.sub(r"[,!?;„“\"']", " ", text.lower())
    lowered = " ".join(_OPEN_FILLER.sub(" ", lowered).split()).strip(" .")
    for pattern in _OPEN_PATTERNS:
        match = pattern.match(lowered)
        if match:
            raw = match.group("term")
            term = app_index.normalize(raw)
            if not term or len(term.split()) > 4 or re.search(r"[\\/:]|\.\w", raw) or _NOT_APPS.search(term):
                return None
            return term
    return None


def match_open_app(text: str) -> Optional[QuickCommand]:
    """open_app for "open <alias | exact app name>", else None (-> LLM)."""
    term = open_term(text)
    if term is None:
        return None
    alias = app_aliases.lookup(term)
    if alias is not None:
        return QuickCommand("open_app", {"name": term}, f"{alias.label} wird geöffnet.")
    entry = app_index.exact(term)
    if entry is not None:
        return QuickCommand("open_app", {"name": entry.name}, f"{entry.name} wird geöffnet.")
    return None
