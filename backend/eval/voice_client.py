"""
Scripted voice client for the live WebSocket pipeline (no microphone needed).

Speech is synthesized with the local Piper voice, resampled to 16 kHz and
streamed in REAL TIME (20 ms frames) like a browser would, so VAD timing
and latencies are realistic. The client also plays the part of the
speaker: it reports playback_started/playback_done and can feed Cypher's
own audio back into the "microphone" (echo test).

Usage (backend running, from backend/):
    python -m eval.voice_client latency          # time to first audio, auto mode
    python -m eval.voice_client wake             # "Hey Cypher, wie spät ist es?" in wake mode
    python -m eval.voice_client bargein          # interrupt by tap and by wake word
    python -m eval.voice_client echo             # own voice played back must not wake Cypher
    python -m eval.voice_client approval         # spoken "ja" to an approval question
    python -m eval.voice_client all
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.security.tokens import get_tokens  # noqa: E402

FRAME = 320  # 20 ms at 16 kHz
URL = "ws://127.0.0.1:8000/api/voice/ws"


def synth_16k(text: str) -> np.ndarray:
    """Test speech. NOTE: the German Piper voice reads "Cypher" as "Zipher";
    scenarios spell it "Saifer" to match how a person actually says it."""
    from app.tts.piper_provider import get_tts  # noqa: PLC0415

    s = get_settings()
    audio = get_tts(s).generate(text, sid=s.PIPER_SPEAKER_ID, speed=s.PIPER_SPEED)
    samples = np.asarray(audio.samples, dtype=np.float32)
    # Linear resample 22050 -> 16000 (good enough for a test signal).
    n_out = int(len(samples) * 16000 / audio.sample_rate)
    return np.interp(np.linspace(0, len(samples) - 1, n_out), np.arange(len(samples)), samples).astype(np.float32)


def resample_22k_to_16k(pcm16: bytes, rate: int) -> np.ndarray:
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
    n_out = int(len(samples) * 16000 / rate)
    return np.interp(np.linspace(0, len(samples) - 1, n_out), np.arange(len(samples)), samples).astype(np.float32)


def to_pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


@dataclass
class Trace:
    t0: float = field(default_factory=time.perf_counter)
    events: List[tuple] = field(default_factory=list)
    audio: List[tuple] = field(default_factory=list)  # (turn, rate, bytes)
    states: List[str] = field(default_factory=list)

    def mark(self, name: str, data: Any = None) -> None:
        self.events.append((round(time.perf_counter() - self.t0, 3), name, data))

    def first(self, name: str, after: float = 0.0) -> Optional[float]:
        for t, n, _ in self.events:
            if n == name and t >= after:
                return t
        return None


class Client:
    def __init__(self, mode: str, echo: bool = False) -> None:
        self.mode = mode
        self.echo = echo
        self.trace = Trace()
        self.ws = None
        self._pending_header: Optional[dict] = None
        self.playback_until = 0.0
        self.echo_queue: List[np.ndarray] = []
        self.turn_audio_seconds: Dict[int, float] = {}
        self.approval: Optional[dict] = None
        self._speech: Optional[np.ndarray] = None
        self._speech_pos = 0
        self._speech_end_t: Optional[float] = None

    async def __aenter__(self):
        self.ws = await websockets.connect(URL, max_size=None)
        await self.ws.send(json.dumps({"type": "auth", "token": get_tokens().user_token}))
        await self.ws.send(json.dumps({"type": "mode", "mode": self.mode}))
        self._reader = asyncio.create_task(self._read())
        self._pump = asyncio.create_task(self._mic_pump())
        await asyncio.sleep(0.3)
        return self

    async def __aexit__(self, *exc):
        self._pump.cancel()
        self._reader.cancel()
        await self.ws.close()

    async def _read(self) -> None:
        async for message in self.ws:
            if isinstance(message, bytes):
                header = self._pending_header or {}
                self._pending_header = None
                rate = header.get("sample_rate", 22050)
                seconds = len(message) / 2 / rate
                turn = header.get("turn_id", 0)
                if turn not in self.turn_audio_seconds:
                    self.turn_audio_seconds[turn] = 0.0
                    self.trace.mark("first_audio", turn)
                    await self.ws.send(json.dumps({"type": "playback_started", "turn_id": turn}))
                self.turn_audio_seconds[turn] += seconds
                self.trace.audio.append((turn, rate, message))
                now = time.perf_counter()
                self.playback_until = max(self.playback_until, now) + seconds
                if self.echo:
                    self.echo_queue.append(resample_22k_to_16k(message, rate) * 0.6)
                continue
            msg = json.loads(message)
            kind = msg.get("type")
            if kind == "audio":
                self._pending_header = msg
            elif kind == "state":
                self.trace.states.append(msg["state"])
                self.trace.mark("state:" + msg["state"], msg.get("turn_id"))
            elif kind == "audio_end":
                self.trace.mark("audio_end", msg)
                turn = msg.get("turn_id")
                asyncio.create_task(self._finish_playback(turn))
            elif kind == "approval":
                self.approval = msg["card"]
                self.trace.mark("approval", msg["card"])
            else:
                self.trace.mark(kind, {k: v for k, v in msg.items() if k != "type"})

    async def _finish_playback(self, turn: int) -> None:
        await asyncio.sleep(max(0.0, self.playback_until - time.perf_counter()))
        try:
            await self.ws.send(json.dumps({"type": "playback_done", "turn_id": turn}))
        except websockets.ConnectionClosed:
            pass

    async def send_json(self, payload: dict) -> None:
        await self.ws.send(json.dumps(payload))

    async def _mic_pump(self) -> None:
        """Like a real microphone: always streams 20 ms frames in real time -
        queued speech if any, otherwise silence (+ echo of Cypher's voice)."""
        next_at = time.perf_counter()
        while True:
            if self._speech is not None and self._speech_pos < len(self._speech):
                frame = self._speech[self._speech_pos : self._speech_pos + FRAME]
                self._speech_pos += FRAME
                if self._speech_pos >= len(self._speech):
                    self._speech_end_t = round(time.perf_counter() - self.trace.t0, 3)
                if len(frame) < FRAME:
                    frame = np.concatenate([frame, np.zeros(FRAME - len(frame), np.float32)])
            else:
                frame = np.zeros(FRAME, np.float32)
            if self.echo_queue:
                echo = self.echo_queue[0]
                take = min(len(echo), FRAME)
                frame = frame.copy()
                frame[:take] += echo[:take]
                self.echo_queue[0] = echo[take:]
                if not len(self.echo_queue[0]):
                    self.echo_queue.pop(0)
            await self.ws.send(to_pcm16(frame))
            next_at += FRAME / 16000
            await asyncio.sleep(max(0.0, next_at - time.perf_counter()))

    async def stream(self, samples: np.ndarray, *, lead_silence: float = 0.3, tail_silence: float = 0.0) -> float:
        """Speak `samples` into the mic; returns trace time when speech ended."""
        await asyncio.sleep(lead_silence)
        self._speech_end_t = None
        self._speech = samples
        self._speech_pos = 0
        while self._speech_end_t is None:
            await asyncio.sleep(0.01)
        end = self._speech_end_t
        await asyncio.sleep(tail_silence)
        return end

    async def silence(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def wait_for(self, name: str, after: float, timeout: float = 60.0) -> Optional[float]:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            t = self.trace.first(name, after)
            if t is not None:
                return t
            await asyncio.sleep(0.05)
        return None

    def texts(self, name: str, after: float = 0.0) -> List[Any]:
        return [d for t, n, d in self.trace.events if n == name and t >= after]


async def scenario_latency(runs: int = 3) -> Dict[str, Any]:
    questions = ["Wie spät ist es?", "Was ist die Hauptstadt von Italien?", "Wie wird das Wetter morgen in Bern?", "Mach die Musik etwas leiser."]
    results = []
    async with Client("auto") as c:
        for q in questions[: max(1, runs + 1)]:
            if "leiser" in q:
                continue  # never change the user's volume in a benchmark
            audio = synth_16k(q)
            await c.send_json({"type": "start"})
            start = round(time.perf_counter() - c.trace.t0, 3)
            speech_end = await c.stream(audio, tail_silence=0.1)
            first_audio = await c.wait_for("first_audio", speech_end)
            transcript = await c.wait_for("transcript", start)
            await c.wait_for("reply", start)
            reply = c.texts("reply", start)
            await c.wait_for("audio_end", speech_end)
            await asyncio.sleep(0.5)
            await c.silence(max(0.0, c.playback_until - time.perf_counter()) + 0.5)
            metrics_ev = [d for d in c.texts("audio_end", start) if d.get("metrics")]
            results.append({
                "question": q,
                "transcript": [d.get("text") for d in c.texts("transcript", start)],
                "reply": reply[-1]["text"] if reply else None,
                "speech_end_to_transcript_s": round(transcript - speech_end, 3) if transcript else None,
                "speech_end_to_first_audio_s": round(first_audio - speech_end, 3) if first_audio else None,
                "server_metrics": metrics_ev[-1]["metrics"] if metrics_ev else None,
            })
    firsts = [r["speech_end_to_first_audio_s"] for r in results if r["speech_end_to_first_audio_s"]]
    return {"scenario": "latency", "runs": results, "median_first_audio_s": statistics.median(firsts) if firsts else None}


async def scenario_wake() -> Dict[str, Any]:
    async with Client("wake") as c:
        await c.silence(0.5)
        # Talk that must NOT wake Cypher.
        neg_end = await c.stream(synth_16k("Ich habe heute Mittag mit meinem Kollegen über Fussball geredet."), tail_silence=1.5)
        woke_by_mistake = c.trace.first("wake", neg_end - 5)
        start = round(time.perf_counter() - c.trace.t0, 3)
        speech_end = await c.stream(synth_16k("Hey Saifer, wie spät ist es?"))
        wake = await c.wait_for("wake", start, 15)
        first_audio = await c.wait_for("first_audio", speech_end, 30)
        await c.wait_for("audio_end", speech_end, 30)
        await c.silence(max(0.0, c.playback_until - time.perf_counter()) + 0.8)
        # Separate wake word, then the command after a pause.
        start2 = round(time.perf_counter() - c.trace.t0, 3)
        await c.stream(synth_16k("Hey Saifer."), tail_silence=0.9)
        listening = await c.wait_for("state:listening", start2, 10)
        end2 = await c.stream(synth_16k("Was ist die Hauptstadt von Frankreich?"))
        first2 = await c.wait_for("first_audio", end2, 30)
        await c.wait_for("audio_end", end2, 30)
        return {
            "scenario": "wake",
            "no_false_wake_on_normal_talk": woke_by_mistake is None,
            "wake_detected": wake is not None,
            "one_breath_reply": [d["text"] for d in c.texts("reply", start)][:1],
            "speech_end_to_first_audio_s": round(first_audio - speech_end, 3) if first_audio else None,
            "two_step_listening_state": listening is not None,
            "two_step_reply": [d["text"] for d in c.texts("reply", start2)][:1],
            "two_step_first_audio_s": round(first2 - end2, 3) if first2 else None,
        }


async def scenario_bargein() -> Dict[str, Any]:
    out: Dict[str, Any] = {"scenario": "bargein"}
    async with Client("wake") as c:
        start = round(time.perf_counter() - c.trace.t0, 3)
        await c.send_json({"type": "text", "text": "Erzähl mir ausführlich in mindestens acht Sätzen etwas über die Geschichte des Mondes."})
        first = await c.wait_for("first_audio", start, 30)
        await asyncio.sleep(1.0)
        t_int = round(time.perf_counter() - c.trace.t0, 3)
        await c.send_json({"type": "interrupt"})
        stop = await c.wait_for("audio_stop", t_int, 5)
        listening = await c.wait_for("state:listening", t_int, 5)
        out["tap"] = {"first_audio": first is not None, "audio_stop_after_s": round(stop - t_int, 3) if stop else None,
                      "back_to_listening": listening is not None}
        await c.silence(1.0)

        start = round(time.perf_counter() - c.trace.t0, 3)
        await c.send_json({"type": "text", "text": "Erzähl mir ausführlich in mindestens acht Sätzen etwas über die Geschichte von Rom."})
        await c.wait_for("first_audio", start, 30)
        await asyncio.sleep(0.5)
        t_wake = round(time.perf_counter() - c.trace.t0, 3)
        end = await c.stream(synth_16k("Hey Saifer, stopp."), tail_silence=1.0)
        stop2 = await c.wait_for("audio_stop", t_wake, 10)
        out["wake_word"] = {"audio_stop_after_speech_end_s": round(stop2 - end, 3) if stop2 else None, "stopped": stop2 is not None}
    return out


async def scenario_echo() -> Dict[str, Any]:
    async with Client("wake", echo=True) as c:
        start = round(time.perf_counter() - c.trace.t0, 3)
        # The answer contains the wake word itself - played back into the mic.
        await c.send_json({"type": "text", "text": "Wie heisst du? Antworte mit einem Satz, der mit 'Hey, ich bin Cypher' beginnt."})
        await c.wait_for("first_audio", start, 30)
        await c.wait_for("audio_end", start, 60)
        await c.silence(max(0.0, c.playback_until - time.perf_counter()) + 2.5)
        interrupted = c.trace.first("audio_stop", start)
        replies = [d["text"] for d in c.texts("reply", start)]
        return {"scenario": "echo", "reply": replies[:1], "self_interrupted": interrupted is not None,
                "wake_events": c.texts("wake", start)}


async def scenario_approval() -> Dict[str, Any]:
    async with Client("auto") as c:
        start = round(time.perf_counter() - c.trace.t0, 3)
        await c.send_json({"type": "text", "text": "Schreib eine Notiz: Sprachtest M3 bestanden."})
        card = await c.wait_for("approval", start, 30)
        await c.wait_for("audio_end", start, 30)
        await c.silence(max(0.0, c.playback_until - time.perf_counter()) + 0.5)
        t_yes = round(time.perf_counter() - c.trace.t0, 3)
        await c.stream(synth_16k("Ja, mach das."))
        resolved = await c.wait_for("approval_resolved", t_yes, 20)
        reply_t = await c.wait_for("reply", t_yes, 30)
        return {"scenario": "approval", "card": c.approval, "resolved": [d for d in c.texts("approval_resolved", t_yes)][:1],
                "reply": [d["text"] for d in c.texts("reply", t_yes)][:1], "ok": bool(card and resolved and reply_t)}


SCENARIOS = {"latency": scenario_latency, "wake": scenario_wake, "bargein": scenario_bargein, "echo": scenario_echo, "approval": scenario_approval}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=[*SCENARIOS, "all"])
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in names:
        result = await SCENARIOS[name]()
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
