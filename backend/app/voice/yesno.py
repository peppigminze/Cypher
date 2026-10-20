"""Spoken answers to an approval question ("ja" / "nein"), keyword based.

Deliberately not NLU: an approval must only be granted on a clear "yes".
Anything ambiguous (both or neither) returns None and Cypher asks again.
"""
from __future__ import annotations

import re
from typing import Optional

_YES = re.compile(r"\b(ja|jep|jup|jawohl|jo|genau|klar|bestätige|bestätigen|bestätigt|mach das|mach es|mach's|leg los|okay|ok|yes|yep|passt)\b", re.IGNORECASE)
_NO = re.compile(r"\b(nein|nee|nö|no|nope|abbrechen|abbruch|stopp|stop|lass es|lass das|lieber nicht|nicht)\b", re.IGNORECASE)


def match_yes_no(text: str) -> Optional[bool]:
    is_yes = bool(_YES.search(text))
    is_no = bool(_NO.search(text))
    if is_yes and not is_no:
        return True
    if is_no and not is_yes:
        return False
    return None


# Critical actions (FIX2): only the explicit phrase "Ja, ausführen" approves.
# Whisper writes it as "Ja, ausführen.", "Ja ausführen", "Ja, aus führen" ...
_CRITICAL_YES = re.compile(r"^\W*ja\W+aus\s?f(ü|ue|u)hr(e|)n\W*$", re.IGNORECASE)


def match_critical_confirmation(text: str) -> Optional[bool]:
    """True only for "Ja, ausführen" (nothing else in the utterance), False
    for a clear no, None otherwise - including a plain "Ja"."""
    cleaned = (text or "").strip()
    if _CRITICAL_YES.match(cleaned):
        return True
    if match_yes_no(cleaned) is False:
        return False
    return None
