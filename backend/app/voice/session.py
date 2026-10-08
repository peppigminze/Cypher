"""
One live voice conversation over a WebSocket.

Wire protocol (all JSON text frames, except raw audio):

  client -> server
    binary                      PCM16 LE mono 16 kHz microphone audio
    {"type":"auth","token","conversation_id"?}  must be the first message
                                (app/api/voice_ws.py); a recent conversation_id is resumed
    {"type":"mode","mode"}      "wake" | "auto" | "ptt"
    {"type":"start"}            tap/press: start listening for a command
    {"type":"stop"}             release (push-to-talk) or "I'm done talking"
    {"type":"interrupt"}        barge-in by tap: stop speaking/thinking now
    {"type":"new_conversation"} forget the short-term context, start fresh
    {"type":"text","text"}      typed command
    {"type":"approval","approval_id","approved"}
    {"type":"playback_started","turn_id"} / {"type":"playback_done","turn_id"}
    {"type":"ping"}

  server -> client
    ready, state, vad, wake, transcript, reply_delta, tool, approval,
    audio (header) + binary PCM16 frame, audio_end, audio_stop, reply,
    notice, error, pong

States: idle | wake_listening | listening | thinking | speaking | confirming

Pipeline per command:
    VAD end-of-speech -> STT -> engine.run_turn (streamed) -> each sentence
    -> TTS -> audio frames out, while the model keeps generating.
Barge-in: a tap (interrupt) or the wake word while thinking/speaking
cancels the turn task (LLM stream + TTS) and tells the client to drop its
audio queue.
Echo guard: browser echo cancellation plus a server check that ignores a
"wake word" which merely repeats what Cypher is saying right now
(ECHO_GUARD=aec_text), or no listening at all while speaking (mic_gate).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from app.agent.engine import (
    ApprovalRequired,
    ConversationEngine,
    Sentence,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnDone,
    TurnFailed,
    new_conversation_id,
    record_turn_metrics,
)
from app.config import Settings
from app.core import errors
from app.core.context import pipeline_id_var
from app.core.events import bus
from app.core.metrics import metrics
from app.tools import pending_approvals
from app.voice.protocols import SAMPLE_RATE, SpeechToText, TextToSpeech, WakeWordDetector
from app.voice.segmenter import Endpointer, SpeechEnd, SpeechPause, SpeechStart
from app.voice.yesno import match_yes_no

logger = logging.getLogger("cypher.voice.session")

MODES = ("wake", "auto", "ptt")
# Whisper sometimes "hears" these in near-silence; never treat them as commands.
_HALLUCINATIONS = re.compile(
    r"^(untertitel.*|vielen dank( fürs zuschauen)?\.?|danke\.?|tschüss\.?|amara\.org.*|\.+|you)$", re.IGNORECASE
)


class VoiceSession:
    def __init__(
        self,
        send_json: Callable[[Dict[str, Any]], Any],
        send_bytes: Callable[[bytes], Any],
        *,
        settings: Settings,
        engine_factory: Callable[[], ConversationEngine],
        stt: SpeechToText,
        tts: TextToSpeech,
        wake: Optional[WakeWordDetector],
        endpointer_factory: Callable[[], Endpointer],
        conversation_id: Optional[str] = None,
    ) -> None:
        self._send_json = send_json
        self._send_bytes = send_bytes
        self.settings = settings
        self.engine_factory = engine_factory
        self.stt = stt
        self.tts = tts
        self.wake = wake if settings.WAKE_WORD_ENABLED else None
        self.endpointer = endpointer_factory()
        self.conversation_id = conversation_id or new_conversation_id()
        self.session_id = uuid.uuid4().hex[:8]

        self.mode = settings.VOICE_DEFAULT_MODE if (settings.VOICE_DEFAULT_MODE != "wake" or self.wake) else "auto"
        self.state = "idle"
        self.turn_id = 0
        self._turn_task: Optional[asyncio.Task] = None
        self._tts_task: Optional[asyncio.Task] = None
        self._sentences: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
        self._ptt_buffer: List[np.ndarray] = []
        self._ptt_active = False
        self._listen_deadline: Optional[float] = None
        self._spoken_recently: List[tuple[float, str]] = []
        self._pending_approval: Optional[ApprovalRequired] = None
        self._audio_out_seconds = 0.0
        self._playback_expected_until = 0.0
        self._utterance_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._t0: Optional[float] = None
        self._turn_metrics: Dict[str, float] = {}
        self._first_audio_sent = False
        self._closed = False
        self._queued_notices: List[str] = []
        # Speculative work started at a short pause: (voiced_frames, task).
        self._speculative: Optional[tuple] = None
        # What the user really heard of the current answer (audio sent), for
        # an honest history on barge-in; progress phrases are not part of it.
        self._turn_engine: Optional[ConversationEngine] = None
        self._heard: List[str] = []
        self._progress_texts: set[str] = set()
        # One lock for all outbound frames: an audio header and its binary
        # frame must arrive back to back.
        self._send_lock = asyncio.Lock()

    # ================================================================ lifecycle
    async def start(self) -> None:
        await self._send({"type": "ready", "session_id": self.session_id, "conversation_id": self.conversation_id, "mode": self.mode,
                          "wake_word_enabled": self.wake is not None, "wake_phrase": self.settings.WAKE_WORD_PHRASE,
                          "input_sample_rate": SAMPLE_RATE})
        await self._set_state(self._resting_state())
        self._spawn(self._reminder_listener())
        self._spawn(self._timeouts())

    async def close(self) -> None:
        self._closed = True
        await self._cancel_turn()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ================================================================ sending
    async def _send(self, payload: Dict[str, Any], binary: Optional[bytes] = None) -> None:
        if self._closed:
            return
        async with self._send_lock:
            try:
                await self._send_json(payload)
                if binary is not None:
                    await self._send_bytes(binary)
            except Exception:  # noqa: BLE001 - client went away; the receive loop will notice
                self._closed = True

    async def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            await self._send({"type": "state", "state": state, "turn_id": self.turn_id})

    def _resting_state(self) -> str:
        return "wake_listening" if self.mode == "wake" else "idle"

    # ================================================================ inbound
    async def handle_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        kind = msg.get("type")
        if kind == "ping":
            await self._send({"type": "pong", "t": msg.get("t")})
        elif kind == "mode" and msg.get("mode") in MODES:
            await self._set_mode(msg["mode"])
        elif kind == "start":
            await self._start_listening()
        elif kind == "stop":
            await self._stop_listening()
        elif kind == "interrupt":
            await self.interrupt("tap")
        elif kind == "new_conversation":
            await self.interrupt("new_conversation", quiet=True)
            self.conversation_id = new_conversation_id()
            await self._send({"type": "conversation", "conversation_id": self.conversation_id})
            await self._set_state(self._resting_state())
        elif kind == "text" and isinstance(msg.get("text"), str) and msg["text"].strip():
            await self.interrupt("new_text", quiet=True)
            self._t0 = time.perf_counter()
            await self._begin_turn(msg["text"].strip()[:4000])
        elif kind == "approval" and isinstance(msg.get("approval_id"), str):
            await self._resolve_approval(msg["approval_id"], bool(msg.get("approved")), via="ui")
        elif kind == "playback_started" and msg.get("turn_id") == self.turn_id and self._t0 is not None:
            if "first_audible" not in self._turn_metrics:
                self._turn_metrics["first_audible"] = round(time.perf_counter() - self._t0, 3)
                metrics.record("e2e_first_audible", self._turn_metrics["first_audible"])
        elif kind == "playback_done" and msg.get("turn_id") == self.turn_id:
            await self._on_playback_done()

    async def handle_audio(self, data: bytes) -> None:
        if len(data) < 2:
            return
        samples = np.frombuffer(data[: len(data) - len(data) % 2], dtype="<i2").astype(np.float32) / 32768.0
        if self.mode == "ptt" and self._ptt_active:
            self._ptt_buffer.append(samples)
            return
        if not self._should_segment():
            return
        for event in self.endpointer.feed(samples):
            if isinstance(event, SpeechStart):
                await self._send({"type": "vad", "speech": True})
            elif isinstance(event, SpeechPause):
                self._start_speculation(event)
            elif isinstance(event, SpeechEnd):
                await self._send({"type": "vad", "speech": False})
                self._spawn(self._on_utterance(event))

    def _should_segment(self) -> bool:
        if self.state in ("listening", "confirming", "wake_listening"):
            return True
        if self.state in ("thinking", "speaking"):
            # Barge-in by voice only makes sense with a wake word and without mic gating.
            return self.wake is not None and self.mode == "wake" and self.settings.ECHO_GUARD == "aec_text"
        return False

    # ================================================================ listening control
    async def _set_mode(self, mode: str) -> None:
        if mode == "wake" and self.wake is None:
            await self._send({"type": "error", "code": "wake_disabled", "message": "Das Aktivierungswort ist ausgeschaltet.", "hint": "WAKE_WORD_ENABLED=true in der .env setzen."})
            return
        self.mode = mode
        self.endpointer.reset()
        await self._send({"type": "mode", "mode": mode})
        if self.state in ("idle", "wake_listening", "listening"):
            await self._set_state(self._resting_state())

    async def _start_listening(self) -> None:
        if self.state in ("thinking", "speaking"):
            await self.interrupt("tap", quiet=True)
        self.endpointer.reset()
        if self.mode == "ptt":
            self._ptt_active = True
            self._ptt_buffer = []
        self._listen_deadline = time.monotonic() + self.settings.LISTEN_TIMEOUT_SECONDS if self.mode != "ptt" else None
        await self._set_state("listening")

    async def _stop_listening(self) -> None:
        if self.mode == "ptt" and self._ptt_active:
            self._ptt_active = False
            audio = np.concatenate(self._ptt_buffer) if self._ptt_buffer else np.zeros(0, dtype=np.float32)
            self._ptt_buffer = []
            if audio.shape[0] < SAMPLE_RATE * 0.25:
                await self._set_state(self._resting_state())
                return
            self._t0 = time.perf_counter()
            self._spawn(self._handle_command_audio(audio))
            return
        event = self.endpointer.force_end()
        if event is not None:
            self._spawn(self._on_utterance(event))
        elif self.state == "listening":
            await self._set_state(self._resting_state())

    # ================================================================ speculation
    def _start_speculation(self, pause: SpeechPause) -> None:
        running = self._speculative
        if running is not None and not running[1].done():
            # One speculative job at a time - long sentences with many short
            # pauses must not pile up transcriptions.
            return
        if self.state == "listening":
            task = asyncio.create_task(self.stt.transcribe(pause.audio))
        elif self.state == "wake_listening" and self.wake is not None:
            task = asyncio.create_task(self._speculative_wake(pause.audio))
        else:
            return
        previous = self._speculative
        if previous is not None:
            previous[1].cancel()
        self._speculative = (pause.voiced_frames, task)

    async def _speculative_wake(self, audio: np.ndarray):
        assert self.wake is not None
        result = await self.wake.detect(audio)
        command = None
        if result.detected and len(result.remainder.split()) >= 2:
            command = await self.stt.transcribe(audio)
        return result, command

    async def _take_speculation(self, event: SpeechEnd):
        """Result of the speculative work if nothing was said after the pause."""
        spec, self._speculative = self._speculative, None
        if spec is None:
            return None
        voiced, task = spec
        if voiced != event.voiced_frames:
            task.cancel()
            return None
        try:
            result = await task
        except Exception:  # noqa: BLE001 - fall back to a normal run
            return None
        metrics.record("stt_speculative_hit", 1.0)
        return result

    # ================================================================ utterances
    async def _on_utterance(self, event: SpeechEnd) -> None:
        t_end = time.perf_counter()
        async with self._utterance_lock:
            if self.state in ("thinking", "speaking"):
                await self._maybe_barge_in(event)
            elif self.state == "confirming":
                await self._handle_confirmation_audio(event.audio)
            elif self.state == "wake_listening":
                await self._handle_wake_candidate(event.audio, t_end, await self._take_speculation(event))
            elif self.state == "listening":
                self._t0 = t_end
                spec = await self._take_speculation(event)
                await self._handle_command_audio(event.audio, transcript=spec)

    async def _handle_wake_candidate(self, audio: np.ndarray, t_end: float, spec=None) -> None:
        assert self.wake is not None
        command = None
        if spec is not None:
            result, command = spec
        else:
            result = await self.wake.detect(audio)
        if not result.detected:
            return
        await self._send({"type": "wake", "detected": True, "score": result.score, "transcript": result.transcript})
        self._t0 = t_end
        if len(result.remainder.split()) >= 2 or (result.remainder and audio.shape[0] > SAMPLE_RATE * 2.2):
            # "Hey Cypher, wie spät ist es" in one breath: the command is in this
            # utterance. Re-transcribe with the accurate model and strip the wake phrase.
            await self._handle_command_audio(audio, strip_wake=True, transcript=command)
        else:
            self._listen_deadline = time.monotonic() + self.settings.LISTEN_TIMEOUT_SECONDS
            await self._set_state("listening")

    async def _maybe_barge_in(self, event: SpeechEnd) -> None:
        if self.wake is None:
            return
        result = await self.wake.detect(event.audio)
        if not result.detected:
            return
        # Compare what follows the wake phrase: "Hey Cypher, stopp" must
        # interrupt even if Cypher itself just said "Hey Cypher".
        if self._is_echo(result.remainder or result.transcript):
            logger.info("Ignored wake word that echoes Cypher's own speech: %r", result.transcript)
            return
        await self.interrupt("wake_word", quiet=True)
        await self._send({"type": "wake", "detected": True, "score": result.score, "transcript": result.transcript})
        self._t0 = time.perf_counter()
        if len(result.remainder.split()) >= 2:
            await self._handle_command_audio(event.audio, strip_wake=True)
        else:
            self._listen_deadline = time.monotonic() + self.settings.LISTEN_TIMEOUT_SECONDS
            await self._set_state("listening")

    def _is_echo(self, transcript: str) -> bool:
        now = time.monotonic()
        self._spoken_recently = [(t, s) for t, s in self._spoken_recently if now - t < 20]
        heard = _norm(transcript)
        if not heard:
            return False
        for _, spoken in self._spoken_recently:
            said = _norm(spoken)
            # Very short answers ("ja") are never treated as echo: they would
            # match inside almost any sentence Cypher just said.
            if (len(heard) >= 6 and heard in said) or SequenceMatcher(None, heard, said).find_longest_match(0, len(heard), 0, len(said)).size >= max(8, int(len(heard) * 0.6)):
                return True
        return False

    async def _transcribe(self, audio: np.ndarray, ready=None) -> Optional[str]:
        started = time.perf_counter()
        transcript = ready if ready is not None else await self.stt.transcribe(audio)
        self._turn_metrics = {"stt": round(time.perf_counter() - started, 3), "stt_speculative": ready is not None}
        text = transcript.text.strip()
        if not text or _HALLUCINATIONS.match(text):
            return None
        return text

    async def _handle_command_audio(self, audio: np.ndarray, strip_wake: bool = False, transcript=None) -> None:
        await self._set_state("thinking")
        try:
            text = await self._transcribe(audio, ready=transcript)
        except Exception as exc:  # noqa: BLE001
            logger.exception("STT failed")
            await self._fail(errors.stt_failed() if not isinstance(exc, errors.CypherError) else exc)
            return
        if text and not strip_wake and self._is_echo(text):
            logger.info("Ignored utterance that echoes Cypher's own speech: %r", text)
            text = None
        if text and strip_wake:
            from app.voice.wakeword import match_wake_phrase  # noqa: PLC0415

            match = match_wake_phrase(text, self.settings.WAKE_WORD_PHRASE, self.settings.WAKE_WORD_THRESHOLD)
            text = match.remainder if match.detected else text
        if not text:
            await self._send({"type": "transcript", "text": "", "turn_id": self.turn_id})
            await self._set_state("listening" if self.mode != "ptt" and self.state != "idle" and self._listen_deadline else self._resting_state())
            return
        await self._begin_turn(text, stt_seconds=self._turn_metrics.get("stt"))

    async def _handle_confirmation_audio(self, audio: np.ndarray) -> None:
        approval = self._pending_approval
        if approval is None:
            await self._set_state(self._resting_state())
            return
        transcript = await self.stt.transcribe(audio)
        if self._is_echo(transcript.text):
            # Our own question ("... Soll ich das machen?") came back through the mic.
            return
        decision = match_yes_no(transcript.text)
        await self._send({"type": "transcript", "text": transcript.text, "turn_id": self.turn_id})
        if decision is None:
            await self._speak_standalone("Bitte sag ja oder nein.")
            return
        await self._resolve_approval(approval.approval_id, decision, via="voice")

    # ================================================================ turns
    async def _begin_turn(self, text: str, stt_seconds: Optional[float] = None) -> None:
        self.turn_id += 1
        self._listen_deadline = None
        self._first_audio_sent = False
        self._audio_out_seconds = 0.0
        if stt_seconds is None:
            self._turn_metrics = {}
        await self._send({"type": "transcript", "text": text, "turn_id": self.turn_id, "stt_ms": int((stt_seconds or 0) * 1000)})
        await self._set_state("thinking")
        engine = self.engine_factory()
        self._turn_engine, self._heard = engine, []
        self._turn_task = asyncio.create_task(self._run_engine(engine.run_turn(self.conversation_id, text, source="voice")))

    async def _run_engine(self, events) -> None:
        pipeline_id_var.set(f"{self.session_id}-{self.turn_id}")
        turn = self.turn_id
        self._sentences = asyncio.Queue()
        self._tts_task = asyncio.create_task(self._tts_worker(turn, self._sentences, record_heard=True))
        approval_pending = False
        try:
            async for event in events:
                if turn != self.turn_id:
                    return
                if isinstance(event, TextDelta):
                    await self._send({"type": "reply_delta", "text": event.text, "turn_id": turn})
                elif isinstance(event, Sentence):
                    if event.index < 0:
                        self._progress_texts.add(event.text)
                    if self._t0 is not None and "first_sentence" not in self._turn_metrics:
                        self._turn_metrics["first_sentence"] = round(time.perf_counter() - self._t0, 3)
                    await self._sentences.put(event.text)
                elif isinstance(event, ToolStarted):
                    await self._send({"type": "tool", "phase": "start", "name": event.name, "summary": event.summary, "turn_id": turn})
                elif isinstance(event, ToolFinished):
                    await self._send({"type": "tool", "phase": "end", "name": event.name, "summary": event.summary, "success": event.success, "error": event.error, "turn_id": turn})
                elif isinstance(event, ApprovalRequired):
                    approval_pending = True
                    self._pending_approval = event
                    await self._send({"type": "approval", "turn_id": turn, "card": {
                        "approval_id": event.approval_id, "tool_name": event.tool_name, "arguments": event.arguments,
                        "summary": event.summary, "risk": event.risk, "expires_in_seconds": event.expires_in_seconds}})
                    await self._sentences.put(event.spoken_prompt)
                elif isinstance(event, TurnDone):
                    self._turn_metrics.update({f"engine_{k}": v for k, v in event.metrics.items()})
                    record_turn_metrics(event.metrics)
                    await self._send({"type": "reply", "text": event.reply, "turn_id": turn})
                elif isinstance(event, TurnFailed):
                    await self._send({"type": "error", "turn_id": turn, **event.error.to_dict()})
                    await self._sentences.put(event.error.spoken)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Voice turn crashed")
            err = errors.internal_error()
            await self._send({"type": "error", "turn_id": turn, **err.to_dict()})
            await self._sentences.put(err.spoken)
        finally:
            # Close the engine's generator NOW (also on barge-in/cancel), so it
            # records the interrupted answer in the history immediately.
            await events.aclose()
            await self._sentences.put(None)
        await self._tts_task
        if turn == self.turn_id:
            await self._finish_turn(turn, confirming=approval_pending)

    async def _tts_worker(self, turn: int, queue: "asyncio.Queue[Optional[str]]", record_heard: bool = False) -> None:
        seq = 0
        while True:
            sentence = await queue.get()
            if sentence is None or turn != self.turn_id:
                break
            try:
                audio = await self.tts.synthesize(sentence)
            except Exception:  # noqa: BLE001 - keep text visible even if the voice fails
                logger.exception("TTS failed for a sentence")
                await self._send({"type": "error", "turn_id": turn, **errors.tts_failed().to_dict()})
                continue
            if turn != self.turn_id or not audio.pcm16:
                continue
            if not self._first_audio_sent:
                self._first_audio_sent = True
                if self._t0 is not None:
                    self._turn_metrics["first_audio_sent"] = round(time.perf_counter() - self._t0, 3)
                    metrics.record("e2e_first_audio", self._turn_metrics["first_audio_sent"])
                if self.state != "confirming":
                    await self._set_state("speaking")
            self._spoken_recently.append((time.monotonic(), sentence))
            await self._send({"type": "audio", "turn_id": turn, "seq": seq, "sample_rate": audio.sample_rate, "bytes": len(audio.pcm16), "text": sentence}, binary=audio.pcm16)
            if record_heard and sentence not in self._progress_texts:
                self._heard.append(sentence)
            seq += 1
            self._audio_out_seconds += audio.seconds
            self._playback_expected_until = max(self._playback_expected_until, time.monotonic()) + audio.seconds

    async def _finish_turn(self, turn: int, confirming: bool) -> None:
        await self._send({"type": "audio_end", "turn_id": turn, "metrics": self._turn_metrics})
        logger.info("Voice turn %s metrics: %s", turn, self._turn_metrics)
        if confirming:
            self._listen_deadline = time.monotonic() + max(self.settings.LISTEN_TIMEOUT_SECONDS, 15)
            await self._set_state("confirming")
        elif not self._first_audio_sent:
            await self._set_state(self._resting_state())
        # Otherwise stay "speaking" until the client reports playback_done (or
        # the expected playback time has passed - see _timeouts()).

    async def _on_playback_done(self) -> None:
        self._playback_expected_until = 0.0
        if self.state == "speaking":
            await self._set_state(self._resting_state())
            await self._flush_notices()

    async def interrupt(self, reason: str, quiet: bool = False) -> None:
        if self.state not in ("thinking", "speaking", "confirming") and self._turn_task is None:
            return
        logger.info("Interrupt (%s) in state %s", reason, self.state)
        old_turn, was = self.turn_id, self.state
        self.turn_id += 1  # stale events/audio of the old turn are dropped
        await self._cancel_turn()
        if self._turn_engine is not None and was in ("thinking", "speaking"):
            self._turn_engine.store.get(self.conversation_id).mark_interrupted(" ".join(self._heard))
        self._turn_engine = None
        self._pending_approval = None
        await self._send({"type": "audio_stop", "turn_id": old_turn, "reason": reason})
        self.endpointer.reset()
        if not quiet:
            if self.mode == "ptt":
                await self._set_state("idle")
            else:
                self._listen_deadline = time.monotonic() + self.settings.LISTEN_TIMEOUT_SECONDS
                await self._set_state("listening")

    async def _cancel_turn(self) -> None:
        for task in (self._turn_task, self._tts_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._turn_task = None
        self._tts_task = None

    # ================================================================ approvals
    async def _resolve_approval(self, approval_id: str, approved: bool, via: str) -> None:
        approval = pending_approvals.pop(approval_id)
        self._pending_approval = None
        if approval is None:
            await self._send({"type": "error", "code": "approval_expired", "message": "Diese Bestätigung ist abgelaufen.", "hint": "Sag den Befehl einfach noch einmal."})
            await self._set_state(self._resting_state())
            return
        self.turn_id += 1
        self._t0 = time.perf_counter()
        self._first_audio_sent = False
        self._turn_metrics = {}
        await self._send({"type": "approval_resolved", "approval_id": approval_id, "approved": approved, "via": via})
        await self._set_state("thinking")
        engine = self.engine_factory()
        self._turn_engine, self._heard = engine, []
        self._turn_task = asyncio.create_task(self._run_engine(engine.resume(approval, approved)))

    # ================================================================ background
    async def _speak_standalone(self, text: str) -> None:
        """Speak a fixed sentence outside the LLM (reminders, "ja oder nein?")."""
        self.turn_id += 1
        turn = self.turn_id
        self._first_audio_sent = False
        queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
        await queue.put(text)
        await queue.put(None)
        previous = self.state
        self._tts_task = asyncio.create_task(self._tts_worker(turn, queue))
        await self._tts_task
        await self._send({"type": "audio_end", "turn_id": turn})
        if previous == "confirming":
            await self._set_state("confirming")

    async def _reminder_listener(self) -> None:
        async for event in bus.subscribe():
            if event.type != "reminder.due":
                continue
            title = event.data.get("title") or "Erinnerung"
            text = f"Dein Timer „{title}“ ist abgelaufen." if event.data.get("is_timer") and title != "Timer" else ("Dein Timer ist abgelaufen." if event.data.get("is_timer") else f"Erinnerung: {title}.")
            await self._send({"type": "notice", "kind": "reminder", "text": text})
            if self.state in ("idle", "wake_listening", "listening"):
                await self._speak_standalone(text)
            else:
                self._queued_notices.append(text)

    async def _flush_notices(self) -> None:
        while self._queued_notices and self.state in ("idle", "wake_listening"):
            await self._speak_standalone(self._queued_notices.pop(0))

    async def _timeouts(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            if self.state == "listening" and self._listen_deadline is not None and now > self._listen_deadline and not self.endpointer.in_speech:
                self._listen_deadline = None
                await self._set_state(self._resting_state())
            elif self.state == "confirming" and self._listen_deadline is not None and now > self._listen_deadline and self._pending_approval is None:
                await self._set_state(self._resting_state())
            elif self.state == "speaking" and self._turn_task is not None and self._turn_task.done() and self._playback_expected_until and now > self._playback_expected_until + 5:
                # Client never reported playback_done (tab in background?) - don't hang.
                self._playback_expected_until = 0.0
                await self._set_state(self._resting_state())

    async def _fail(self, err: errors.CypherError) -> None:
        await self._send({"type": "error", "turn_id": self.turn_id, **err.to_dict()})
        await self._speak_standalone(err.spoken)
        await self._set_state(self._resting_state())


def _norm(text: str) -> str:
    return re.sub(r"[^a-zäöüß0-9]+", "", text.lower())
