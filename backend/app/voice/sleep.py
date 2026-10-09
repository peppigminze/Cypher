"""
"Cypher, schlaf" - end the follow-up listening window right away.

Matched like the wake word (app/voice/wakeword.py): the name is compared
phonetically ("Saifer", "Zeifer", "Cypher"), because that is how Whisper
writes it. The rest is deliberately strict, so normal sentences never put
Cypher to sleep by accident:

  * the utterance needs the name AND the word "schlaf"/"schlafen"
    (exact - "geschlafen" does not count),
  * every other word must be a filler ("hey", "jetzt", "bitte", "geh" ...),
  * "Ich habe schlecht geschlafen" or "Cypher, ich hab schlecht geschlafen"
    therefore never match.

Never sent to the LLM - the session handles it directly.
"""
from __future__ import annotations

import re

from app.voice.wakeword import _FILLERS, _similarity, phonetic

_SLEEP_WORDS = {"schlaf", "schlafen", "schlaff"}
_EXTRA_FILLERS = {"jetzt", "bitte", "geh", "gehe", "mal", "du", "kannst", "dann", "so", "und", "wieder", "nun"}
_NAME_SIMILARITY = 0.85  # same bar as the wake word ("Super" ~ 0.8 must not count)


def _name_keys(phrase: str) -> set[str]:
    words = [w for w in re.findall(r"[\wäöüÄÖÜß]+", phrase.lower()) if w not in _FILLERS] or ["cypher"]
    name = "".join(words)
    # written form ("sifer") and how it is spoken ("saifer")
    return {phonetic(name), phonetic(name.replace("y", "ai"))}


def match_sleep_command(transcript: str, phrase: str = "Hey Cypher") -> bool:
    words = [w.lower() for w in re.findall(r"[\wäöüÄÖÜß]+", transcript)]
    if not words or len(words) > 6:
        return False
    keys = _name_keys(phrase)
    has_name = has_sleep = False
    for word in words:
        if word in _SLEEP_WORDS:
            has_sleep = True
        elif word in _FILLERS or word in _EXTRA_FILLERS:
            continue
        elif max(_similarity(phonetic(word), key) for key in keys) >= _NAME_SIMILARITY:
            has_name = True
        else:
            return False  # a real word that isn't part of the command
    return has_name and has_sleep
