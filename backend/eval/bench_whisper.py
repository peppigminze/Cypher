"""
Whisper (faster-whisper, CPU) benchmark: latency and word error rate
(WER) for German voice commands across model sizes / thread counts.

Test audio: by default the German sentences below are synthesized with
the local Piper voice (clean + a noisy copy at ~15 dB SNR). That is
synthetic speech, so absolute WER is optimistic compared to a real
microphone - use --wav-dir with your own recordings (file name = the
reference text's id, plus a matching <id>.txt with the spoken text)
for a realistic number.

Usage (from backend/, venv active):
    python -m eval.bench_whisper --models base small --threads 4 8
    python -m eval.bench_whisper --wav-dir C:/recordings --models small
"""
from __future__ import annotations

import argparse
import io
import json
import re
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

EVAL_DIR = Path(__file__).resolve().parent

SENTENCES = {
    "s01": "Hey Cypher, wie spät ist es?",
    "s02": "Mach die Musik ein bisschen leiser.",
    "s03": "Stell einen Timer auf zehn Minuten.",
    "s04": "Such meine Bewerbung vom letzten Jahr.",
    "s05": "Öffne Spotify und spiel den nächsten Song.",
    "s06": "Merk dir, dass ich am Freitag Berufsschule habe.",
    "s07": "Wie wird das Wetter morgen in Zürich?",
    "s08": "Schreib eine Notiz: Milch und Brot kaufen.",
    "s09": "Kopier den Text in die Zwischenablage.",
    "s10": "Wie stark ist mein Prozessor gerade ausgelastet?",
    "s11": "Ja, mach das.",
    "s12": "Nein, abbrechen.",
}


def normalize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\wäöüß ]+", " ", text)
    return text.split()


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    # Classic Levenshtein distance over words.
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
            prev, d[j] = d[j], cur
    return d[len(hyp)] / max(len(ref), 1)


def synthesize_corpus() -> dict[str, tuple[str, bytes]]:
    sys.path.insert(0, str(EVAL_DIR.parent))
    from app.tts.piper_provider import synthesize_speech  # noqa: PLC0415

    corpus: dict[str, tuple[str, bytes]] = {}
    rng = np.random.default_rng(42)
    for sid, text in SENTENCES.items():
        wav_bytes = synthesize_speech(text).wav_bytes
        corpus[sid] = (text, wav_bytes)
        corpus[f"{sid}-noisy"] = (text, _add_noise(wav_bytes, rng, snr_db=15.0))
    return corpus


def _add_noise(wav_bytes: bytes, rng: np.random.Generator, snr_db: float) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate = w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    signal_power = np.mean(samples**2) + 1e-9
    noise = rng.normal(0, np.sqrt(signal_power / (10 ** (snr_db / 10))), samples.shape)
    noisy = np.clip(samples + noise, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(noisy.tobytes())
    return buf.getvalue()


def load_wav_dir(path: Path) -> dict[str, tuple[str, bytes]]:
    corpus = {}
    for wav in sorted(path.glob("*.wav")):
        ref = wav.with_suffix(".txt")
        if ref.exists():
            corpus[wav.stem] = (ref.read_text(encoding="utf-8").strip(), wav.read_bytes())
    return corpus


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["base", "small"])
    parser.add_argument("--threads", nargs="+", type=int, default=[4, 8])
    parser.add_argument("--beam", nargs="+", type=int, default=[1, 5])
    parser.add_argument("--initial-prompt", default="Cypher, Sprachassistent.")
    parser.add_argument("--wav-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    from faster_whisper import WhisperModel  # noqa: PLC0415

    corpus = load_wav_dir(args.wav_dir) if args.wav_dir else synthesize_corpus()
    print(f"corpus: {len(corpus)} clips", flush=True)

    rows = []
    for size in args.models:
        for threads in args.threads:
            t0 = time.perf_counter()
            model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=threads)
            load_s = time.perf_counter() - t0
            for beam in args.beam:
                for prompt in (None, args.initial_prompt):
                    latencies, wers, rtfs, misses = [], [], [], []
                    for cid, (ref, audio) in corpus.items():
                        t = time.perf_counter()
                        segments, info = model.transcribe(
                            io.BytesIO(audio), language="de", beam_size=beam, initial_prompt=prompt, vad_filter=False
                        )
                        hyp = "".join(s.text for s in segments).strip()
                        dt = time.perf_counter() - t
                        latencies.append(dt)
                        rtfs.append(dt / max(info.duration, 0.01))
                        w = wer(ref, hyp)
                        wers.append(w)
                        if w > 0:
                            misses.append(f"{cid}: {hyp!r}")
                    row = {
                        "model": size,
                        "threads": threads,
                        "beam": beam,
                        "initial_prompt": bool(prompt),
                        "load_s": round(load_s, 2),
                        "latency_median_s": round(statistics.median(latencies), 3),
                        "latency_max_s": round(max(latencies), 3),
                        "rtf_median": round(statistics.median(rtfs), 3),
                        "wer_mean": round(statistics.mean(wers), 3),
                        "errors": misses,
                    }
                    rows.append(row)
                    print(json.dumps({k: v for k, v in row.items() if k != "errors"}), flush=True)
                    for m in misses:
                        print("    ", m, flush=True)
            del model

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (args.out / f"whisper-{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["| Modell | Threads | Beam | initial_prompt | Load s | Latenz Median s | Latenz Max s | RTF | WER |", "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(
                f"| {r['model']} | {r['threads']} | {r['beam']} | {'ja' if r['initial_prompt'] else 'nein'} | {r['load_s']} | "
                f"{r['latency_median_s']} | {r['latency_max_s']} | {r['rtf_median']} | {r['wer_mean']*100:.1f} % |"
            )
        (args.out / f"whisper-{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
