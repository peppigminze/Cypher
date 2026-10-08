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
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


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
