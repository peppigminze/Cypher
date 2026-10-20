"""
Voice approval of CRITICAL actions (FIX2 3c): only "Ja, ausführen" counts.

    plain "Ja"          -> hint, nothing runs, still waiting
    "Ja, ausführen"     -> runs
    "Nein"              -> rejected
    while the question plays, the mic is muted (the question contains the
    phrase - its echo must never approve)
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List

import pytest

from app.agent.engine import ConversationEngine, ConversationStore
from app.config import get_settings
from app.security import policy
from app.security.classifier import CRITICAL, verdict
from app.tools import pending_approvals
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry
from app.voice.session import CRITICAL_PLAIN_YES_HINT, CRITICAL_STT_PROMPT, VoiceSession
from app.voice.segmenter import Endpointer
from app.voice.yesno import match_critical_confirmation

from tests.test_engine import EchoTool, ScriptedLLM, call
from tests.test_voice_session import AmplitudeVAD, FakeSTT, FakeTTS, pcm


class DeleteFolderTool(EchoTool):
    name = "delete_folder_dummy"
    confirm_writes = False

    def classify(self, args, ctx=None):
        return verdict(CRITICAL, "löscht einen ganzen Ordner", "folder_delete")

    def describe(self, args):
        return f"Ordner löschen: {args.get('text', '')}"

    async def execute(self, db=None, **kwargs):
        self.runs.append(kwargs)
        return ToolResult(success=True, data={"deleted": True})


class Harness:
    def __init__(self, llm_steps, stt):
        self.out: List[Dict[str, Any]] = []
        self.registry = ToolRegistry()
        self.registry.register(DeleteFolderTool())
        self.llm = ScriptedLLM(llm_steps)
        self.stt = FakeSTT(stt)
        self.tts = FakeTTS()
        settings = get_settings().model_copy(update={"VOICE_DEFAULT_MODE": "auto", "WAKE_WORD_ENABLED": False})

        async def send_json(payload):
            self.out.append(payload)

        async def send_bytes(_data):
            pass

        self.session = VoiceSession(
            send_json, send_bytes, settings=settings,
            engine_factory=lambda: ConversationEngine(self.llm, self.registry, settings=settings, store=ConversationStore(), tool_names=["delete_folder_dummy"]),
            stt=self.stt, tts=self.tts, wake=None,
            endpointer_factory=lambda: Endpointer(AmplitudeVAD(), silence_ms=320, min_speech_ms=128, preroll_ms=96),
            conversation_id="voice-critical", followup_seconds=lambda: 0.0,
        )

    async def text(self, **msg):
        await self.session.handle_text(json.dumps(msg))

    async def say(self, ms: int = 400):
        for chunk in (pcm(ms, True), pcm(500, False)):
            await self.session.handle_audio(chunk)

    def of(self, kind):
        return [m for m in self.out if m["type"] == kind]

    async def until(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError(f"timeout; messages: {[m['type'] for m in self.out]}")
            await asyncio.sleep(0.005)

    async def question_played(self):
        """The client reports the end of playback; wait out the echo tail."""
        await self.text(type="playback_done", turn_id=self.session.turn_id)
        await self.until(lambda: time.monotonic() > self.session._mute_until)

    @property
    def runs(self):
        return self.registry.get("delete_folder_dummy").runs


@pytest.fixture(autouse=True)
def _clean():
    pending_approvals.clear()
    policy.reset_session_state()
    yield
    pending_approvals.clear()


@pytest.fixture()
async def make():
    created = []

    def factory(*args, **kwargs):
        h = Harness(*args, **kwargs)
        created.append(h)
        return h

    yield factory
    for h in created:
        await h.session.close()


async def _ask(h: Harness):
    await h.session.start()
    await h.text(type="text", text="Lösch den Ordner Alt")
    await h.until(lambda: h.session.state == "confirming")
    card = h.of("approval")[0]["card"]
    assert card["kind"] == "critical" and card["voice_phrase"] == "Ja, ausführen" and card["reason"] == "löscht einen ganzen Ordner"
    assert h.tts.spoken[0].endswith("sag: Ja, ausführen.")
    return card


async def test_plain_yes_is_not_enough(make):
    h = make([[call("delete_folder_dummy", {"text": "Alt"})], ["Gelöscht."]], stt=["Ja.", "Ja, ausführen."])
    await _ask(h)
    await h.question_played()
    await h.say()
    await h.until(lambda: CRITICAL_PLAIN_YES_HINT in h.tts.spoken)
    assert h.runs == [] and h.session.state == "confirming" and not h.of("approval_resolved")
    await h.question_played()
    await h.say()
    await h.until(lambda: h.of("reply"))
    assert h.of("approval_resolved")[0]["approved"] is True and h.runs == [{"text": "Alt"}]
    assert h.stt.prompts[-1] == CRITICAL_STT_PROMPT  # Whisper is primed for the phrase


async def test_no_rejects(make):
    h = make([[call("delete_folder_dummy", {"text": "Alt"})], ["Okay, nicht gelöscht."]], stt=["Nein."])
    await _ask(h)
    await h.question_played()
    await h.say()
    await h.until(lambda: h.of("approval_resolved"))
    assert h.of("approval_resolved")[0]["approved"] is False and h.runs == []


async def test_mic_is_muted_while_the_question_plays(make):
    h = make([[call("delete_folder_dummy", {"text": "Alt"})], ["Gelöscht."]], stt=["Ja, ausführen."])
    await _ask(h)
    assert h.session._mute_until > time.monotonic()  # question still "playing"
    await h.say()  # e.g. the speaker's own "... sag: Ja, ausführen." in the mic
    await asyncio.sleep(0.1)
    assert h.stt.prompts == [] and h.runs == []  # never even transcribed


async def test_click_on_the_card_still_works(make):
    h = make([[call("delete_folder_dummy", {"text": "Alt"})], ["Gelöscht."]], stt=[])
    card = await _ask(h)
    await h.text(type="approval", approval_id=card["approval_id"], approved=True)
    await h.until(lambda: h.of("reply"))
    assert h.runs == [{"text": "Alt"}]


def test_phrase_matcher():
    assert match_critical_confirmation("Ja, ausführen.") is True
    assert match_critical_confirmation("ja ausfuehren") is True
    assert match_critical_confirmation("Ja.") is None
    assert match_critical_confirmation("Ja, mach das.") is None
    assert match_critical_confirmation("Wenn ich das wirklich machen soll, sag: Ja, ausführen.") is None  # full echo
    assert match_critical_confirmation("Nein, lieber nicht.") is False
