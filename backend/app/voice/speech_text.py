"""
Make text speakable before it goes to the TTS voice.

LLMs write for screens: Markdown, emojis, "14:32", "10.6 °C", URLs. The
Piper voice would read asterisks, say "vierzehn Doppelpunkt zweiunddreissig"
or spell out a link. This normalises the common cases; the persona
(app/persona.py) also asks the model to avoid them in the first place.
"""
from __future__ import annotations

import re

_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]

_REPLACEMENTS = [
    (re.compile(r"\bz\.\s?B\.", re.IGNORECASE), "zum Beispiel"),
    (re.compile(r"\bd\.\s?h\.", re.IGNORECASE), "das heisst"),
    (re.compile(r"\bu\.\s?a\.", re.IGNORECASE), "unter anderem"),
    (re.compile(r"\busw\."), "und so weiter"),
    (re.compile(r"\bbzw\."), "beziehungsweise"),
    (re.compile(r"\bca\."), "circa"),
    (re.compile(r"\bNr\."), "Nummer"),
    (re.compile(r"\bevtl\."), "eventuell"),
    (re.compile(r"\binkl\."), "inklusive"),
    (re.compile(r"°\s?C\b"), " Grad"),
    (re.compile(r"°"), " Grad"),
    (re.compile(r"\s?%"), " Prozent"),
    (re.compile(r"\bkm/h\b"), " Kilometer pro Stunde"),
    (re.compile(r"€"), " Euro"),
    (re.compile(r"\bCHF\b"), "Franken"),
    (re.compile(r"&"), " und "),
    (re.compile(r"\s?/\s?"), " "),
]

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]+",
    flags=re.UNICODE,
)


def _time(match: re.Match) -> str:
    hours, minutes = int(match.group(1)), int(match.group(2))
    return f"{hours} Uhr" if minutes == 0 else f"{hours} Uhr {minutes}"


def _date(match: re.Match) -> str:
    day, month = int(match.group(1)), int(match.group(2))
    year = match.group(3)
    if not 1 <= month <= 12:
        return match.group(0)
    return f"{day}. {_MONTHS[month - 1]}" + (f" {year}" if year else "")


def to_speakable(text: str) -> str:
    t = text
    # Markdown: links -> their text, drop formatting characters and headings.
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "einen Link", t)
    t = re.sub(r"`{1,3}[^`]*`{1,3}", lambda m: m.group(0).strip("`"), t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.MULTILINE)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"(?<!\w)[*_](\S[^*_]*\S|\S)[*_](?!\w)", r"\1", t)
    t = _EMOJI.sub("", t)
    # Dates before times so "07.10.2026" isn't read as a time.
    t = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?\b", _date, t)
    t = re.sub(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\s*Uhr\b", _time, t)
    t = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", _time, t)
    # English decimal point -> German decimal comma ("10.6" -> "10,6").
    t = re.sub(r"\b(\d+)\.(\d+)\b", r"\1,\2", t)
    for pattern, replacement in _REPLACEMENTS:
        t = pattern.sub(replacement, t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    return t.strip()
