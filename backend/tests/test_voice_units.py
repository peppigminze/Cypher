"""Unit tests for the voice pipeline's pure logic: sentence splitting,
speakable text, yes/no, wake phrase matching and VAD endpointing."""
from __future__ import annotations

from typing import List

import numpy as np
import pytest

from app.voice.protocols import SAMPLE_RATE
from app.voice.segmenter import Endpointer, SpeechEnd, SpeechPause, SpeechStart
from app.voice.sentences import SentenceSplitter
from app.voice.speech_text import to_speakable
from app.voice.wakeword import WhisperWakeWord, match_wake_phrase, phonetic
from app.voice.yesno import match_yes_no


# ------------------------------------------------------------------ sentences
def _stream(text: str, chunk: int = 3) -> List[str]:
    splitter = SentenceSplitter()
    out: List[str] = []
    for i in range(0, len(text), chunk):
        out += splitter.push(text[i : i + chunk])
    return out + splitter.flush()


def test_sentences_are_emitted_while_streaming():
    splitter = SentenceSplitter()
    assert splitter.push("Hallo Silvan. Wie") == ["Hallo Silvan."]
    assert splitter.push(" geht's?") == []  # end of buffer: could continue
    assert splitter.push(" Gut.") == ["Wie geht's?"]
    assert splitter.flush() == ["Gut."]


@pytest.mark.parametrize(
    "text, first",
    [
        ("Das ist z. B. ein Test. Zweiter Satz.", "Das ist z. B. ein Test."),
        ("Es sind 3.5 Grad. Kalt.", "Es sind 3.5 Grad."),
        ("Am 1. Oktober ist frei. Schön.", "Am 1. Oktober ist frei."),
        ("Morgen in Bern 15 °C. Dann Regen.", "Morgen in Bern 15 °C."),
    ],
)
def test_no_split_inside_abbreviations_numbers_and_ordinals(text, first):
    assert _stream(text)[0] == first


def test_long_first_chunk_is_cut_at_a_comma_for_faster_first_audio():
    parts = _stream("Ich habe mir das gerade angeschaut und finde, dass es eigentlich ganz gut aussieht und")
    assert parts[0].endswith(",") and len(parts) == 2


# ------------------------------------------------------------------ speakable text
@pytest.mark.parametrize(
    "raw, spoken",
    [
        ("Es ist 14:32 Uhr.", "Es ist 14 Uhr 32."),
        ("Um 9:00 geht's los.", "Um 9 Uhr geht's los."),
        ("Morgen 10.6 °C.", "Morgen 10,6 Grad."),
        ("**Wichtig:** Lies [das hier](https://x.y).", "Wichtig: Lies das hier."),
        ("Mehr unter https://example.com/a?b=c", "Mehr unter einen Link"),
        ("Super gemacht 🎉👍", "Super gemacht"),
        ("Am 07.10.2026 ist Termin.", "Am 7. Oktober 2026 ist Termin."),
        ("- Milch\n- Brot", "Milch Brot"),
        ("Lautstärke 30%", "Lautstärke 30 Prozent"),
    ],
)
def test_to_speakable(raw, spoken):
    assert to_speakable(raw) == spoken


# ------------------------------------------------------------------ yes / no
@pytest.mark.parametrize(
    "text, expected",
    [("Ja.", True), ("Ja, mach das.", True), ("Okay", True), ("Nein.", False), ("Lieber nicht.", False),
     ("Ja, nein, weiss nicht.", None), ("Wie spät ist es?", None)],
)
def test_match_yes_no(text, expected):
    assert match_yes_no(text) is expected


# ------------------------------------------------------------------ wake phrase
def test_phonetic_key_merges_whisper_spellings():
    assert phonetic("Cypher") == phonetic("Sypher") == phonetic("Ziffer")
    assert phonetic("Saifer") == phonetic("Seifer") == phonetic("Seyfer")


@pytest.mark.parametrize(
    "transcript, remainder",
    [
        ("Hey Cypher, wie spät ist es?", "wie spät ist es"),
        ("Hey Saifer, mach leiser.", "mach leiser"),
        ("Cypher.", ""),
        ("Okay Sypher wie wird das Wetter", "wie wird das Wetter"),
        ("Karl Cypher.", ""),  # tiny Whisper mishearing a lone "Hey Saifer."
        ("Halsaifer stop.", "stop"),  # M8 acceptance run: "Hey Saifer, stopp." merged, scored 0.82 before
        ("Heiseife Stop.", "Stop"),
    ],
)
def test_wake_phrase_detected_with_remainder(transcript, remainder):
    result = match_wake_phrase(transcript, "Hey Cypher", 0.85)
    assert result.detected and result.remainder == remainder


@pytest.mark.parametrize(
    "transcript",
    ["Ich hab gestern mit Cypher geredet.", "Super, danke.", "Seifert kommt morgen.", "Hey Silvan, alles gut?", "Karl Cypher kommt morgen.",
     "Kaffee.", "", "Hallo Seifert, kommst du morgen?", "Halt, Seife!"],
)
def test_wake_phrase_not_detected(transcript):
    assert not match_wake_phrase(transcript, "Hey Cypher", 0.85).detected


async def test_whisper_wake_word_only_transcribes_the_window():
    seen = []
    wake = WhisperWakeWord("Hey Cypher", "tiny", 0.85, window_seconds=1.0, stt_fn=lambda audio: seen.append(len(audio)) or "Hey Cypher, Pause")
    result = await wake.detect(np.zeros(SAMPLE_RATE * 3, dtype=np.float32))
    assert result.detected and result.remainder == "Pause" and seen == [SAMPLE_RATE]


# ------------------------------------------------------------------ endpointing
class ScriptedVAD:
    """VAD double: one probability per 512-sample frame."""

    frame_samples = 512

    def __init__(self, probabilities: List[float]) -> None:
        self.probabilities = list(probabilities)
        self.resets = 0

    def speech_probability(self, frame: np.ndarray) -> float:
        return self.probabilities.pop(0) if self.probabilities else 0.0

    def reset(self) -> None:
        self.resets += 1


FRAME = 512
FRAME_MS = FRAME * 1000 / SAMPLE_RATE  # 32 ms


def _run(probabilities: List[float], **kwargs):
    endpointer = Endpointer(ScriptedVAD(probabilities), **{"silence_ms": 320, "min_speech_ms": 128, "preroll_ms": 96, **kwargs})
    events = []
    for _ in probabilities:
        events += endpointer.feed(np.zeros(FRAME, dtype=np.float32))
    return endpointer, events


def test_endpointer_finds_one_utterance_with_preroll():
    _, events = _run([0.0] * 5 + [0.9] * 20 + [0.0] * 12)
    kinds = [type(e) for e in events]
    assert kinds == [SpeechStart, SpeechEnd]
    end = events[1]
    # 3 frames pre-roll + 20 voiced + a little silence (trailing silence trimmed)
    assert end.voiced_frames == 20 and not end.truncated
    assert 20 * FRAME <= end.audio.shape[0] <= 28 * FRAME


def test_single_click_does_not_open_an_utterance():
    _, events = _run([0.0, 0.95, 0.0, 0.0, 0.95, 0.0] + [0.0] * 12)
    assert events == []


def test_too_short_speech_is_dropped_as_noise():
    _, events = _run([0.9] * 3 + [0.0] * 12)
    assert [type(e) for e in events] == [SpeechStart]


def test_early_pause_lets_stt_start_speculatively():
    _, events = _run([0.9] * 10 + [0.0] * 4 + [0.9] * 5 + [0.0] * 12, early_pause_ms=96)
    kinds = [type(e) for e in events]
    assert kinds == [SpeechStart, SpeechPause, SpeechPause, SpeechEnd]
    # Speaker continued after the first pause: the end has more voiced
    # frames, so the session knows that speculative transcript is stale -
    # the second pause covers the whole utterance and is used.
    assert events[1].voiced_frames < events[3].voiced_frames == events[2].voiced_frames


def test_max_length_truncates():
    _, events = _run([0.9] * 40, max_utterance_seconds=0.64)
    end = [e for e in events if isinstance(e, SpeechEnd)][0]
    assert end.truncated


def test_force_end_for_push_to_talk():
    endpointer, events = _run([0.9] * 10)
    assert [type(e) for e in events] == [SpeechStart]
    end = endpointer.force_end()
    assert isinstance(end, SpeechEnd) and end.voiced_frames == 10
    assert endpointer.force_end() is None
