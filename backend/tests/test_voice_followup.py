"""Follow-up listening window (FIX2 point 1): after an answer has finished
PLAYING, Cypher keeps listening without the wake word for a while.

Same harness as test_voice_session.py (real engine with a scripted LLM, real
endpointer, fake STT/TTS/wake word). Windows are short here (0.8 s) so the
tests stay fast; the echo tail (FOLLOWUP_ECHO_TAIL_SECONDS) is 0.35 s."""
from __future__ import annotations

import asyncio

import pytest

from app.voice.session import FOLLOWUP_ECHO_TAIL_SECONDS, SLEEP_ACK
from app.voice.sleep import match_sleep_command

from tests.test_engine import call
from tests.test_voice_session import FakeWake, harnesses, pcm  # noqa: F401 - fixture

WINDOW = 0.8


def followup_states(h):
    return [m for m in h.of("state") if "followup_seconds" in m]


async def answered(h, text="Hallo"):
    """Typed question, wait until the answer is generated (not yet played)."""
    await h.text(type="text", text=text)
    await h.until(lambda: h.session.state == "speaking" and h.session._turn_task is not None and h.session._turn_task.done())


async def played(h):
    await h.text(type="playback_done", turn_id=h.session.turn_id)


async def past_echo_tail():
    await asyncio.sleep(FOLLOWUP_ECHO_TAIL_SECONDS + 0.05)


# ------------------------------------------------------------------ opening and closing
async def test_window_opens_when_playback_ends_not_when_generation_ends(harnesses):
    h = harnesses([["Hallo Silvan."]], wake=FakeWake([]), mode="wake", followup=WINDOW)
    await h.session.start()
    await answered(h)
    await asyncio.sleep(0.2)
    # generated and sent, but the browser is still playing it
    assert h.session.state == "speaking" and followup_states(h) == []
    await played(h)
    state = followup_states(h)[-1]
    assert state["state"] == "listening" and state["followup_seconds"] == WINDOW and state["followup_total"] == WINDOW


async def test_follow_up_question_needs_no_wake_word_and_restarts_the_window(harnesses):
    wake = FakeWake([])  # never consulted inside the window
    h = harnesses([["Zwölf Uhr."], ["Rom."]], stt=["Und was ist die Hauptstadt von Italien?"], wake=wake, mode="wake", followup=WINDOW)
    await h.session.start()
    await answered(h, "Wie spät ist es?")
    await played(h)
    await past_echo_tail()
    await h.say()
    await h.until(lambda: len(h.of("reply")) == 2)
    assert h.of("wake") == [] and h.llm.calls[1][-1]["content"].endswith("Und was ist die Hauptstadt von Italien?")
    await h.until(lambda: h.session.state == "speaking" and h.session._turn_task.done())
    await played(h)
    assert len(followup_states(h)) == 2  # a fresh window after the follow-up answer


async def test_window_expires_back_to_the_wake_word(harnesses):
    h = harnesses([["Hallo."]], wake=FakeWake([]), mode="wake", followup=WINDOW)
    await h.session.start()
    await answered(h)
    await played(h)
    assert h.session.state == "listening"
    await h.until(lambda: h.session.state == "wake_listening", timeout=WINDOW + 1.0)
    assert h.session._followup_until is None


async def test_utterance_across_the_deadline_is_not_cut_off(harnesses):
    h = harnesses([["Hallo."], ["Klar, mache ich."]], stt=["Kannst du mir noch etwas über den Mond erzählen?"], wake=FakeWake([]), mode="wake", followup=WINDOW)
    await h.session.start()
    await answered(h)
    await played(h)
    await asyncio.sleep(WINDOW - 0.3)  # talk starts inside the window ...
    await h.session.handle_audio(pcm(300, True))
    assert h.of("followup")[-1] == {"type": "followup", "active": False, "reason": "speech"}
    await asyncio.sleep(0.6)  # ... and goes on past the deadline
    assert h.session.state == "listening"
    await h.session.handle_audio(pcm(400, True))
    await h.session.handle_audio(pcm(500, False))
    await h.until(lambda: len(h.of("reply")) == 2)
    assert h.of("transcript")[-1]["text"] == "Kannst du mir noch etwas über den Mond erzählen?"


async def test_no_window_when_switched_off(harnesses):
    h = harnesses([["Hallo."]], wake=FakeWake([]), mode="wake", followup=0)
    await h.session.start()
    await answered(h)
    await played(h)
    assert h.session.state == "wake_listening" and followup_states(h) == []


async def test_no_window_outside_wake_mode(harnesses):
    h = harnesses([["Hallo."]], mode="auto", followup=WINDOW)
    await h.session.start()
    await answered(h)
    await played(h)
    assert h.session.state == "idle" and followup_states(h) == []


# ------------------------------------------------------------------ sleep command
async def test_sleep_command_ends_the_window_without_the_llm(harnesses):
    h = harnesses([["Hallo."]], stt=["Cypher, schlaf."], wake=FakeWake([]), mode="wake", followup=5.0)
    await h.session.start()
    await answered(h)
    await played(h)
    await past_echo_tail()
    await h.say()
    await h.until(lambda: SLEEP_ACK in h.tts.spoken)
    assert len(h.llm.calls) == 1  # only the first question went to the model
    await h.text(type="playback_done", turn_id=h.session.turn_id)
    assert h.session.state == "wake_listening" and len(followup_states(h)) == 1  # no new window after "Gut."


async def test_normal_sentence_about_sleep_is_a_normal_question(harnesses):
    h = harnesses([["Hallo."], ["Das tut mir leid."]], stt=["Ich habe schlecht geschlafen"], wake=FakeWake([]), mode="wake", followup=5.0)
    await h.session.start()
    await answered(h)
    await played(h)
    await past_echo_tail()
    await h.say()
    await h.until(lambda: len(h.of("reply")) == 2)
    assert SLEEP_ACK not in h.tts.spoken and h.llm.calls[1][-1]["content"].endswith("Ich habe schlecht geschlafen")


@pytest.mark.parametrize("text", ["Cypher schlaf", "Cypher, schlaf.", "Schlaf, Cypher!", "Cypher schlafen", "Saifer, schlaf", "Hey Cypher, schlaf jetzt", "Cypher, geh schlafen"])
def test_sleep_command_variants(text):
    assert match_sleep_command(text)


@pytest.mark.parametrize("text", ["Ich habe schlecht geschlafen", "Cypher, ich hab schlecht geschlafen", "Schlaf gut", "Wie lange soll ich schlafen?", "Super schlaf", "Seifert schläft", "schlafen", "Cypher, wie spät ist es?"])
def test_no_false_sleep_command(text):
    assert not match_sleep_command(text)


# ------------------------------------------------------------------ echo, barge-in, approvals
async def test_own_voice_in_the_window_is_not_a_command(harnesses):
    answer = "Sag einfach Hey Cypher, dann höre ich dir zu."
    h = harnesses([[answer]], stt=[answer], wake=FakeWake([]), mode="wake", followup=1.2)
    await h.session.start()
    await answered(h, "Wie wecke ich dich?")
    await played(h)
    # the echo tail right after playback is not even segmented
    await h.session.handle_audio(pcm(400, True))
    await h.session.handle_audio(pcm(400, False))
    assert h.of("vad") == []
    await past_echo_tail()
    await h.say()  # the speakers still echo our own sentence
    await h.until(lambda: len(followup_states(h)) == 2)  # window resumed, not a new turn
    assert len(h.llm.calls) == 1 and h.of("transcript")[-1]["text"] == ""
    await h.until(lambda: h.session.state == "wake_listening", timeout=2.0)


async def test_barge_in_on_a_follow_up_answer_still_works(harnesses):
    h = harnesses([["Hallo."], ["Erster Satz. Zweiter Satz. Dritter Satz."]], stt=["Erzähl mir was"], wake=FakeWake([]), mode="wake", followup=5.0)
    await h.session.start()
    await answered(h)
    await played(h)
    await past_echo_tail()
    h.tts.gate = asyncio.Event()  # the follow-up answer stays "speaking"
    h.tts.spoken.clear()
    await h.say()
    await h.until(lambda: h.session.state == "speaking")
    await h.text(type="interrupt")
    await h.until(lambda: h.of("audio_stop"))
    assert h.of("audio_stop")[0]["reason"] == "tap" and h.session.state == "listening"
    assert h.session._followup_until is None  # a tap interrupt is not a follow-up window


async def test_window_starts_only_after_the_approval_decision(harnesses):
    h = harnesses([[call("save_note", {"text": "Brot"})], ["Gespeichert."]], stt=["Ja."], wake=FakeWake([]), mode="wake", followup=5.0)
    await h.session.start()
    await h.text(type="text", text="Notiere Brot")
    await h.until(lambda: h.session.state == "confirming")
    await h.text(type="playback_done", turn_id=h.session.turn_id)  # the question was read out
    assert h.session.state == "confirming" and followup_states(h) == []
    await h.say(300)
    await h.until(lambda: h.of("reply") and h.session._turn_task is not None and h.session._turn_task.done())
    await played(h)
    assert followup_states(h)[-1]["state"] == "listening"


# ------------------------------------------------------------------ menu setting
def test_followup_window_setting_roundtrip(client, user_headers):
    from app.database.db import session_scope
    from app.database.models import RuntimeSetting
    from app.settings_store import FOLLOWUP_WINDOW_KEY

    try:
        assert client.get("/api/settings/voice", headers=user_headers).json()["followup_window_seconds"] == 10.0  # .env default
        resp = client.put("/api/settings/voice", headers=user_headers, json={"followup_window_seconds": 15})
        assert resp.status_code == 200 and resp.json()["followup_window_seconds"] == 15.0
        assert client.get("/api/settings/voice", headers=user_headers).json()["followup_window_seconds"] == 15.0
        assert client.put("/api/settings/voice", headers=user_headers, json={"followup_window_seconds": 0}).json()["followup_window_seconds"] == 0.0
        assert client.put("/api/settings/voice", headers=user_headers, json={"followup_window_seconds": 99}).status_code == 422
        assert client.put("/api/settings/voice", json={"followup_window_seconds": 5}).status_code == 401
    finally:
        with session_scope() as db:
            row = db.get(RuntimeSetting, FOLLOWUP_WINDOW_KEY)
            if row is not None:
                db.delete(row)


async def test_tap_ends_the_window(harnesses):
    h = harnesses([["Hallo."]], wake=FakeWake([]), mode="wake", followup=5.0)
    await h.session.start()
    await answered(h)
    await played(h)
    await h.text(type="stop")  # tap on the bubble while it listens
    assert h.session.state == "wake_listening" and h.session._followup_until is None


# ------------------------------------------------------------------ found by the live run
async def test_one_breath_longer_than_the_wake_window_keeps_the_command(harnesses):
    # tiny only heard the first 2.5 s ("Hi Cypher."), the question came after
    wake = FakeWake(["Hi Cypher."])
    h = harnesses([["Zwölf Uhr."]], stt=["Hey Cypher, wie spät ist es?"], wake=wake, mode="wake", followup=0)
    await h.session.start()
    await h.say(3000)
    await h.until(lambda: h.of("reply"))
    assert h.of("transcript")[-1]["text"] == "wie spät ist es"


async def test_lone_wake_word_in_the_window_is_not_sent_to_the_model(harnesses):
    h = harnesses([["Hallo."], ["Zwölf Uhr."]], stt=["Hey Cypher.", "Wie spät ist es?"], wake=FakeWake([]), mode="wake", followup=5.0)
    await h.session.start()
    await answered(h)
    await played(h)
    await past_echo_tail()
    await h.say()
    await h.until(lambda: h.of("wake"))
    assert len(h.llm.calls) == 1 and h.session.state == "listening" and h.session._followup_until is None
    await h.say()  # now the actual question
    await h.until(lambda: len(h.of("reply")) == 2)
    assert h.llm.calls[1][-1]["content"].endswith("Wie spät ist es?")


async def test_wake_phrase_in_front_of_a_follow_up_question_is_stripped(harnesses):
    h = harnesses([["Hallo."], ["Zwölf Uhr."]], stt=["Hey Cypher, wie spät ist es?"], wake=FakeWake([]), mode="wake", followup=5.0)
    await h.session.start()
    await answered(h)
    await played(h)
    await past_echo_tail()
    await h.say()
    await h.until(lambda: len(h.of("reply")) == 2)
    assert h.of("transcript")[-1]["text"] == "wie spät ist es"


async def test_command_transcription_knows_the_sleep_phrase_in_wake_mode(harnesses):
    h = harnesses([["Hallo."]], stt=["Cypher, schlaf."], wake=FakeWake([]), mode="wake", followup=5.0)
    await h.session.start()
    await answered(h)
    await played(h)
    await past_echo_tail()
    await h.say()
    await h.until(lambda: SLEEP_ACK in h.tts.spoken)
    assert h.stt.prompts and all("schlaf" in (p or "") for p in h.stt.prompts)
