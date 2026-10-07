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
