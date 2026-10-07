"""
Incremental sentence splitter for streamed LLM output.

TTS starts as soon as the first sentence is complete, while the model
keeps generating - this is what turns "wait for the whole answer" into
"Cypher starts talking after a fraction of a second".

German-aware: no split after common abbreviations ("z. B.", "ca.", "Nr.")
or inside numbers/times ("3.5", "14.30", "1. Oktober"). The very first
chunk may already be cut at a comma once it is long enough, to get the
first audio out even faster.
"""
from __future__ import annotations

import re
from typing import List

_ABBREVIATIONS = {
    "z.b", "zb", "d.h", "dh", "u.a", "ua", "usw", "bzw", "ca", "nr", "dr", "prof", "evtl", "inkl", "ggf",
    "vgl", "etc", "z", "b", "mio", "mrd", "st", "str", "tel", "u", "o", "s", "min", "std", "sek", "jan",
    "feb", "mär", "apr", "jun", "jul", "aug", "sep", "sept", "okt", "nov", "dez",
}
_SENTENCE_END = re.compile(r"([.!?…]+)[\"'»“”)]*(\s+|$)")


class SentenceSplitter:
    def __init__(self, first_chunk_min_chars: int = 40, max_chunk_chars: int = 220) -> None:
        self._buffer = ""
        self._emitted_any = False
        self.first_chunk_min_chars = first_chunk_min_chars
        self.max_chunk_chars = max_chunk_chars

    def push(self, text: str) -> List[str]:
        self._buffer += text
        out: List[str] = []
        while True:
            sentence = self._take()
            if sentence is None:
                break
            out.append(sentence)
        return out

    def flush(self) -> List[str]:
        rest = self._buffer.strip()
        self._buffer = ""
        return [rest] if rest else []

    def _take(self) -> str | None:
        buf = self._buffer
        for match in _SENTENCE_END.finditer(buf):
            end = match.end(1)
            if match.group(2) == "" and match.end() == len(buf):
                # Punctuation at the very end of the buffer: the next token
                # might continue it ("3." -> "3.5"), wait for more text.
                continue
            if self._is_abbreviation_or_number(buf, match.start(1)):
                continue
            return self._cut(match.end())
        if not self._emitted_any and len(buf) >= self.first_chunk_min_chars:
            comma = max(buf.rfind(", "), buf.rfind("; "), buf.rfind(": "))
            if comma >= self.first_chunk_min_chars - 10:
                return self._cut(comma + 2)
        if len(buf) >= self.max_chunk_chars:
            cut = max(buf.rfind(", ", 0, self.max_chunk_chars), buf.rfind(" ", 0, self.max_chunk_chars))
            return self._cut(cut + 1 if cut > 0 else self.max_chunk_chars)
        return None

    def _cut(self, index: int) -> str:
        sentence, self._buffer = self._buffer[:index].strip(), self._buffer[index:]
        self._emitted_any = True
        return sentence

    @staticmethod
    def _is_abbreviation_or_number(buf: str, punct_index: int) -> bool:
        if buf[punct_index] != ".":
            return False
        before = buf[:punct_index]
        word = re.search(r"([\wäöüÄÖÜß.]+)$", before)
        if word:
            token = word.group(1).lower().rstrip(".")
            if token in _ABBREVIATIONS:
                return True
            if token.isdigit():
                # "1. Oktober" / "3. Platz": an ordinal, not the end of a sentence.
                after = buf[punct_index + 1 :].lstrip()
                return bool(after) and (after[0].isdigit() or after[0].islower() or after.split(" ")[0] in _MONTHS)
        return False


_MONTHS = {"Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"}
