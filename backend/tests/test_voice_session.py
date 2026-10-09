"""VoiceSession (app/voice/session.py) end to end with fakes: real engine
(scripted LLM), real endpointer (amplitude VAD), fake STT/TTS/wake word.
Covers typed + spoken turns, push-to-talk, barge-in with honest history,
spoken approvals, the echo guard and the wake word."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pytest

from app.agent.engine import INTERRUPTED_MARK, ConversationEngine, ConversationStore
from app.config import get_settings
from app.tools import pending_approvals
from app.tools.registry import ToolRegistry
from app.voice.protocols import SAMPLE_RATE, SpeechAudio, Transcript, WakeResult
from app.voice.segmenter import Endpointer
from app.voice.session import VoiceSession
from app.voice.wakeword import match_wake_phrase

from tests.test_engine import NoteTool, ScriptedLLM, call


# ------------------------------------------------------------------ fakes
class FakeSTT:
    def __init__(self, texts: List[str]) -> None:
        self.texts = list(texts)
        self.prompts: List[Optional[str]] = []

    async def transcribe(self, audio, *, initial_prompt=None):
        self.prompts.append(initial_prompt)
        text = self.texts.pop(0) if self.texts else ""
        return Transcript(text, "de", audio.shape[0] / SAMPLE_RATE, 0.01)


class FakeTTS:
    """Synthesizes instantly; `gate` (if set) blocks every sentence after the first."""

    def __init__(self) -> None:
        self.spoken: List[str] = []
        self.gate: Optional[asyncio.Event] = None

    async def synthesize(self, text):
        if self.gate is not None and self.spoken:
            await self.gate.wait()
        self.spoken.append(text)
        return SpeechAudio(pcm16=b"\x01\x00" * 2205, sample_rate=22050, seconds=0.1, synth_seconds=0.001)


class FakeWake:
    """Wake word = the next scripted transcript, matched like the real one."""

    def __init__(self, transcripts: List[str]) -> None:
        self.transcripts = list(transcripts)

    async def detect(self, audio):
        text = self.transcripts.pop(0) if self.transcripts else ""
        return match_wake_phrase(text, "Hey Cypher", 0.85)


class AmplitudeVAD:
    frame_samples = 512

    def speech_probability(self, frame):
        return 0.9 if float(np.abs(frame).mean()) > 0.05 else 0.0

    def reset(self):
        pass


def pcm(ms: int, loud: bool) -> bytes:
    samples = np.full(int(SAMPLE_RATE * ms / 1000), 0.5 if loud else 0.0, dtype=np.float32)
    return (samples * 32767).astype("<i2").tobytes()


class Harness:
    def __init__(self, llm_steps, stt=(), wake=None, mode="auto", followup=0.0, **settings_update):
        self.out: List[Dict[str, Any]] = []
        self.binary: List[bytes] = []
        self.store = ConversationStore()
        self.llm = ScriptedLLM(llm_steps)
        registry = ToolRegistry()
        registry.register(NoteTool())
        self.registry = registry
        self.stt = FakeSTT(list(stt))
        self.tts = FakeTTS()
        settings = get_settings().model_copy(update={"VOICE_DEFAULT_MODE": mode, "WAKE_WORD_ENABLED": wake is not None, **settings_update})

        async def send_json(payload):
            self.out.append(payload)

        async def send_bytes(data):
            self.binary.append(data)

        self.session = VoiceSession(
            send_json,
            send_bytes,
            settings=settings,
            engine_factory=lambda: ConversationEngine(self.llm, registry, settings=settings, store=self.store, tool_names=["save_note"]),
            stt=self.stt,
            tts=self.tts,
            wake=wake,
            endpointer_factory=lambda: Endpointer(AmplitudeVAD(), silence_ms=320, min_speech_ms=128, preroll_ms=96),
            conversation_id="voice-test",
            followup_seconds=lambda: followup,
        )

    async def text(self, **msg):
        await self.session.handle_text(json.dumps(msg))

    async def say(self, ms: int = 600):
        """Speech followed by enough silence to end the utterance."""
        for chunk in (pcm(ms, True), pcm(500, False)):
            await self.session.handle_audio(chunk)

    def of(self, kind: str) -> List[Dict[str, Any]]:
        return [m for m in self.out if m["type"] == kind]

    def states(self) -> List[str]:
        return [m["state"] for m in self.of("state")]

    async def until(self, predicate: Callable[[], bool], timeout: float = 2.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"timeout; messages: {[m['type'] for m in self.out]}")
            await asyncio.sleep(0.005)

    def history(self):
        return self.store.get("voice-test").messages


@pytest.fixture(autouse=True)
def _clean():
    pending_approvals.clear()
    yield
    pending_approvals.clear()


@pytest.fixture()
async def harnesses():
    created: List[Harness] = []

    def make(*args, **kwargs) -> Harness:
        h = Harness(*args, **kwargs)
        created.append(h)
        return h

    yield make
    for h in created:
        await h.session.close()


# ------------------------------------------------------------------ turns
async def test_typed_turn_streams_sentence_audio(harnesses):
    h = harnesses([["Hallo Silvan. Alles klar bei mir."]])
    await h.session.start()
    await h.text(type="text", text="Hallo")
    await h.until(lambda: h.of("audio_end"))
    assert [a["text"] for a in h.of("audio")] == ["Hallo Silvan.", "Alles klar bei mir."]
    assert len(h.binary) == 2 and h.of("reply")[0]["text"] == "Hallo Silvan. Alles klar bei mir."
    assert "thinking" in h.states() and h.states()[-1] == "speaking"
    await h.text(type="playback_done", turn_id=h.session.turn_id)
    assert h.session.state == "idle"


async def test_spoken_command_in_auto_mode(harnesses):
    h = harnesses([["Es ist zwölf Uhr."]], stt=["Wie spät ist es?"])
    await h.session.start()
    await h.text(type="start")
    assert h.session.state == "listening"
    await h.say()
    await h.until(lambda: h.of("audio_end"))
    assert h.of("transcript")[0]["text"] == "Wie spät ist es?"
    assert h.of("vad")[0] == {"type": "vad", "speech": True}
    assert h.llm.calls[0][-1]["content"].endswith("Wie spät ist es?")


async def test_push_to_talk(harnesses):
    h = harnesses([["Okay."]], stt=["Test eins zwei"], mode="ptt")
    await h.session.start()
    await h.text(type="start")
    await h.session.handle_audio(pcm(800, True))
    await h.text(type="stop")
    await h.until(lambda: h.of("audio_end"))
    assert h.of("transcript")[0]["text"] == "Test eins zwei"


async def test_whisper_hallucination_in_silence_is_ignored(harnesses):
    h = harnesses([], stt=["Untertitel im Auftrag des ZDF"])
    await h.session.start()
    await h.text(type="start")
    await h.say()
    await h.until(lambda: h.of("transcript"))
    assert h.of("transcript")[0]["text"] == "" and h.llm.calls == []


# ------------------------------------------------------------------ barge-in
async def test_tap_interrupt_keeps_only_what_was_heard(harnesses):
    h = harnesses([["Erster Satz. Zweiter Satz. Dritter Satz."]])
    h.tts.gate = asyncio.Event()  # sentence 2 never finishes synthesizing
    await h.session.start()
    await h.text(type="text", text="Erzähl was")
    await h.until(lambda: h.of("audio") and h.of("reply"))
    await h.text(type="interrupt")
    assert h.of("audio_stop") and h.session.state == "listening"
    assert h.history()[-1] == {"role": "assistant", "content": "Erster Satz." + INTERRUPTED_MARK}


async def test_new_typed_text_interrupts_the_running_turn(harnesses):
    h = harnesses([["Eins. Zwei. Drei."], ["Neu."]])
    h.tts.gate = asyncio.Event()
    await h.session.start()
    await h.text(type="text", text="Alt")
    await h.until(lambda: h.of("audio"))
    h.tts.gate = None
    await h.text(type="text", text="Neu")
    await h.until(lambda: [r for r in h.of("reply") if r["text"] == "Neu."])
    texts = [m["content"] for m in h.history() if m["role"] == "assistant"]
    assert texts == ["Eins." + INTERRUPTED_MARK, "Neu."]


# ------------------------------------------------------------------ approvals
async def test_spoken_yes_approves_the_action(harnesses):
    h = harnesses([[call("save_note", {"text": "Brot"})], ["Gespeichert."]], stt=["Ja."])
    await h.session.start()
    await h.text(type="text", text="Notiere Brot")
    await h.until(lambda: h.session.state == "confirming")
    card = h.of("approval")[0]["card"]
    assert card["tool_name"] == "save_note" and card["risk"] == "write"
    assert h.of("audio")[0]["text"].endswith("Soll ich das machen?")
    assert h.registry.get("save_note").runs == []
    await h.say(300)
    await h.until(lambda: h.of("reply"))
    assert h.of("approval_resolved")[0]["via"] == "voice"
    assert h.registry.get("save_note").runs == [{"text": "Brot"}]
    assert h.of("reply")[-1]["text"] == "Gespeichert."


async def test_unclear_answer_asks_again_and_no_rejects(harnesses):
    h = harnesses([[call("save_note", {"text": "Brot"})]], stt=["Vielleicht morgen", "Nein."])
    await h.session.start()
    await h.text(type="text", text="Notiere Brot")
    await h.until(lambda: h.session.state == "confirming")
    await h.say(300)
    await h.until(lambda: "Bitte sag ja oder nein." in h.tts.spoken)
    await h.say(300)
    await h.until(lambda: h.of("approval_resolved"))
    assert h.of("approval_resolved")[0]["approved"] is False
    assert h.registry.get("save_note").runs == []


async def test_approval_by_click(harnesses):
    h = harnesses([[call("save_note", {"text": "Milch"})], ["Ist notiert."]])
    await h.session.start()
    await h.text(type="text", text="Notiere Milch")
    await h.until(lambda: h.of("approval"))
    await h.text(type="approval", approval_id=h.of("approval")[0]["card"]["approval_id"], approved=True)
    await h.until(lambda: h.of("reply"))
    assert h.registry.get("save_note").runs == [{"text": "Milch"}]


# ------------------------------------------------------------------ wake word + echo
async def test_wake_word_with_command_in_one_breath(harnesses):
    wake = FakeWake(["Hey Cypher, wie spät ist es"])
    h = harnesses([["Zwölf Uhr."]], stt=["Hey Cypher, wie spät ist es?"], wake=wake, mode="wake")
    await h.session.start()
    assert h.session.state == "wake_listening"
    await h.say(2600)
    await h.until(lambda: h.of("audio_end"))
    assert h.of("wake")[0]["detected"] is True
    assert h.of("transcript")[0]["text"] == "wie spät ist es"


async def test_no_wake_word_no_turn(harnesses):
    h = harnesses([], wake=FakeWake(["Ich hab gestern mit Cypher geredet"]), mode="wake")
    await h.session.start()
    await h.say()
    await asyncio.sleep(0.05)
    assert h.of("wake") == [] and h.llm.calls == [] and h.session.state == "wake_listening"


async def test_own_voice_through_the_speakers_is_not_a_barge_in(harnesses):
    wake = FakeWake(["Hey Cypher, dann höre ich dir zu", "Hey Cypher stopp"])
    h = harnesses([["Sag einfach Hey Cypher, dann höre ich dir zu. Noch was."]], wake=wake, mode="wake")
    h.tts.gate = asyncio.Event()
    await h.session.start()
    await h.text(type="text", text="Wie wecke ich dich?")
    await h.until(lambda: h.session.state == "speaking")
    await h.say()  # the mic hears Cypher's own sentence
    await asyncio.sleep(0.05)
    assert h.of("audio_stop") == [] and h.session.state == "speaking"
    await h.say()  # now the user really says the wake word
    await h.until(lambda: h.of("audio_stop"))
    assert h.of("audio_stop")[0]["reason"] == "wake_word"


# ------------------------------------------------------------------ cloud only after asking (M5)
async def test_voice_offers_the_cloud_after_a_local_failure(harnesses, monkeypatch):
    from app.agent import cloud_offer
    from app.llm.base import LLMUnavailableError

    class DownLLM(ScriptedLLM):
        async def stream_chat(self, messages, tools=None, **kwargs):
            raise LLMUnavailableError("down", provider="local", base_url="http://127.0.0.1:11434")
            yield  # pragma: no cover

    monkeypatch.setattr(cloud_offer, "cloud_available", lambda settings=None: True)
    h = harnesses([], stt=["Ja."])
    cloud_llm = ScriptedLLM([["Hier ist die Cloud."]])

    def factory(provider=None):
        if provider == "cloud":
            return ConversationEngine(cloud_llm, h.registry, provider_name="cloud", settings=h.session.settings, store=h.store, tool_names=[])
        return ConversationEngine(DownLLM([]), h.registry, provider_name="local", settings=h.session.settings, store=h.store, tool_names=[])

    h.session.engine_factory = factory
    await h.session.start()
    await h.text(type="text", text="Erklär mir Ebbe und Flut")
    await h.until(lambda: h.session.state == "confirming")
    assert h.of("approval")[0]["card"]["risk"] == "cloud"
    assert cloud_offer.SPOKEN_QUESTION in h.tts.spoken and cloud_llm.calls == []
    await h.say(300)  # "Ja."
    await h.until(lambda: [r for r in h.of("reply") if r["text"] == "Hier ist die Cloud."])
    assert [m["role"] for m in cloud_llm.calls[0][1:]] == ["user"]
