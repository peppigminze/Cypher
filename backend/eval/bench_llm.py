"""
LLM benchmark for Cypher: speed (load time, time-to-first-token,
tokens/s), VRAM footprint, and German tool-calling accuracy.

Talks to Ollama's native /api/chat directly (not through the backend)
so model choice can be evaluated before/independently of the app.

Usage (from backend/, venv active):
    python -m eval.bench_llm qwen2.5:7b qwen3:14b --num-ctx 8192
    python -m eval.bench_llm qwen3.5:9b --skip-speed --out ../docs/overhaul/bench

Scoring per case (see eval/tool_cases.json):
    correct      right tool and every checked argument matches
    wrong_args   right tool, at least one checked argument wrong/missing
    wrong_tool   a different tool than expected (and not in 'alt')
    missed       no tool called although one was expected
    spurious     a tool was called although none was expected
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = EVAL_DIR / "tool_cases.json"

SPEED_PROMPT = "Erkläre in drei kurzen Sätzen, was ein lokaler Sprachassistent ist."

# Kept close to the production prompt (persona + tool rules) so the
# accuracy numbers transfer; the production prompt lives in app/persona.py.
SYSTEM_PROMPT = (
    "Du bist Cypher, ein lokal laufender persönlicher Sprachassistent. Antworte knapp auf Deutsch. "
    "Nutze die verfügbaren Tools, wenn sie helfen, statt zu raten. Rufe höchstens ein Tool pro Schritt auf. "
    "Wenn keine Aktion nötig ist (Smalltalk, Allgemeinwissen, Rechnen), antworte direkt ohne Tool."
)


@dataclass
class CaseResult:
    id: str
    prompt: str
    expected: str | None
    got_tool: str | None
    got_args: dict[str, Any]
    outcome: str
    latency_s: float
    reply: str = ""


@dataclass
class ModelReport:
    model: str
    num_ctx: int
    load_s: float | None = None
    size_total_gb: float | None = None
    size_vram_gb: float | None = None
    gpu_share: float | None = None
    system_vram_used_gb: float | None = None
    ttft_s: list[float] = field(default_factory=list)
    gen_tps: list[float] = field(default_factory=list)
    prompt_tps: list[float] = field(default_factory=list)
    cases: list[CaseResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for c in self.cases:
            counts[c.outcome] = counts.get(c.outcome, 0) + 1
        n = len(self.cases) or 1
        lat = [c.latency_s for c in self.cases]
        return {
            "model": self.model,
            "num_ctx": self.num_ctx,
            "load_s": self.load_s,
            "size_total_gb": self.size_total_gb,
            "size_vram_gb": self.size_vram_gb,
            "gpu_share": self.gpu_share,
            "system_vram_used_gb": self.system_vram_used_gb,
            "ttft_s_median": statistics.median(self.ttft_s) if self.ttft_s else None,
            "gen_tps_median": statistics.median(self.gen_tps) if self.gen_tps else None,
            "prompt_tps_median": statistics.median(self.prompt_tps) if self.prompt_tps else None,
            "tool_accuracy": counts.get("correct", 0) / n if self.cases else None,
            "outcomes": counts,
            "tool_latency_s_median": statistics.median(lat) if lat else None,
            "errors": self.errors,
        }


class Ollama:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def unload_all(self) -> None:
        ps = self.client.get(f"{self.base_url}/api/ps").json()
        for m in ps.get("models", []):
            self.client.post(f"{self.base_url}/api/generate", json={"model": m["name"], "keep_alive": 0})
        # Unloading is asynchronous on the server side.
        for _ in range(30):
            if not self.client.get(f"{self.base_url}/api/ps").json().get("models"):
                return
            time.sleep(0.5)

    def ps(self, model: str) -> dict[str, Any] | None:
        for m in self.client.get(f"{self.base_url}/api/ps").json().get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return m
        return None

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self.client.post(f"{self.base_url}/api/chat", json=payload)
        if resp.status_code == 400 and "think" in resp.text and "think" in payload:
            # Models without a thinking mode reject the field entirely.
            payload = {k: v for k, v in payload.items() if k != "think"}
            resp = self.client.post(f"{self.base_url}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()

    def chat_stream_ttft(self, payload: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        started = time.perf_counter()
        ttft: float | None = None
        final: dict[str, Any] = {}
        payload = {**payload, "stream": True}
        with self.client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
            if resp.status_code == 400:
                resp.read()
                if "think" in resp.text:
                    payload.pop("think", None)
                    return self.chat_stream_ttft(payload)
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                content = (chunk.get("message") or {}).get("content") or ""
                if ttft is None and content.strip():
                    ttft = time.perf_counter() - started
                if chunk.get("done"):
                    final = chunk
        return (ttft if ttft is not None else time.perf_counter() - started), final


def system_dedicated_vram_gb() -> float | None:
    """Total dedicated VRAM in use on the whole system (all processes),
    via the Windows 'GPU Adapter Memory' perf counter. None off-Windows."""
    if sys.platform != "win32":
        return None
    cmd = (
        "(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage').CounterSamples | "
        "Measure-Object -Property CookedValue -Maximum | Select-Object -ExpandProperty Maximum"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=30)
        return round(float(out.stdout.strip().replace(",", ".")) / 1e9, 2)
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def _matches(expected_values: list[Any], actual: Any) -> bool:
    for ev in expected_values:
        if isinstance(ev, bool):
            if actual is ev or str(actual).lower() == str(ev).lower():
                return True
        elif isinstance(ev, (int, float)):
            try:
                if float(actual) == float(ev):
                    return True
            except (TypeError, ValueError):
                continue
        elif isinstance(actual, str) and str(ev).lower() in actual.lower():
            return True
    return False


def score_case(case: dict[str, Any], tool: str | None, args: dict[str, Any]) -> str:
    expected = case.get("expect")
    if expected is None:
        return "spurious" if tool and tool not in case.get("alt", []) else "correct"
    if tool is None:
        return "missed"
    if tool != expected and tool not in case.get("alt", []):
        return "wrong_tool"
    for name, values in (case.get("args") or {}).items():
        if not _matches(values, args.get(name)):
            return "wrong_args"
    return "correct"


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def run_model(ollama: Ollama, model: str, suite: dict[str, Any], tools: list[dict[str, Any]], args: argparse.Namespace) -> ModelReport:
    report = ModelReport(model=model, num_ctx=args.num_ctx)
    options = {"num_ctx": args.num_ctx, "temperature": 0}
    base = {"model": model, "think": False, "keep_alive": "10m", "options": options}

    ollama.unload_all()
    t0 = time.perf_counter()
    try:
        first = ollama.chat({**base, "stream": False, "messages": [{"role": "user", "content": "Hallo"}]})
    except httpx.HTTPError as exc:
        report.errors.append(f"load failed: {exc}")
        return report
    report.load_s = round(first.get("load_duration", 0) / 1e9 or (time.perf_counter() - t0), 2)

    info = ollama.ps(model)
    if info:
        report.size_total_gb = round(info.get("size", 0) / 1e9, 2)
        report.size_vram_gb = round(info.get("size_vram", 0) / 1e9, 2)
        if info.get("size"):
            report.gpu_share = round(info.get("size_vram", 0) / info["size"], 3)
    report.system_vram_used_gb = system_dedicated_vram_gb()

    if not args.skip_speed:
        for _ in range(args.speed_runs):
            ttft, final = ollama.chat_stream_ttft(
                {**base, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": SPEED_PROMPT}]}
            )
            report.ttft_s.append(round(ttft, 3))
            if final.get("eval_count") and final.get("eval_duration"):
                report.gen_tps.append(round(final["eval_count"] / (final["eval_duration"] / 1e9), 1))
            if final.get("prompt_eval_count") and final.get("prompt_eval_duration"):
                report.prompt_tps.append(round(final["prompt_eval_count"] / (final["prompt_eval_duration"] / 1e9), 1))

    if not args.skip_tools:
        for case in suite["cases"]:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": case["prompt"]}]
            t0 = time.perf_counter()
            try:
                resp = ollama.chat({**base, "stream": False, "messages": messages, "tools": tools})
            except httpx.HTTPError as exc:
                report.errors.append(f"{case['id']}: {exc}")
                report.cases.append(CaseResult(case["id"], case["prompt"], case.get("expect"), None, {}, "error", 0.0))
                continue
            latency = time.perf_counter() - t0
            msg = resp.get("message") or {}
            calls = msg.get("tool_calls") or []
            tool = calls[0]["function"]["name"] if calls else None
            targs = _parse_args(calls[0]["function"].get("arguments")) if calls else {}
            outcome = score_case(case, tool, targs)
            report.cases.append(
                CaseResult(case["id"], case["prompt"], case.get("expect"), tool, targs, outcome, round(latency, 3), (msg.get("content") or "")[:200])
            )
            print(f"  [{model}] {case['id']:<14} -> {outcome:<10} tool={tool} args={targs}", flush=True)

    return report


def load_tools(suite: dict[str, Any], use_registry: bool) -> list[dict[str, Any]]:
    if use_registry:
        # Live tool schemas from the backend - see app/tools/ for the source of truth.
        sys.path.insert(0, str(EVAL_DIR.parent))
        from app.tools.catalog import openai_tool_schemas  # noqa: PLC0415

        return openai_tool_schemas()
    return [{"type": "function", "function": t} for t in suite["catalog"]]


def write_markdown(reports: list[ModelReport], path: Path) -> None:
    lines = [
        "| Modell | Load s | VRAM GB (Modell) | GPU-Anteil | VRAM System GB | TTFT s | Gen tok/s | Prompt tok/s | Tool-Trefferquote | Ø Tool-Latenz s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        s = r.summary()
        acc = f"{s['tool_accuracy']*100:.0f} % ({s['outcomes'].get('correct', 0)}/{len(r.cases)})" if s["tool_accuracy"] is not None else "-"
        lines.append(
            f"| {r.model} | {s['load_s']} | {s['size_vram_gb']} | {s['gpu_share']} | {s['system_vram_used_gb']} | "
            f"{s['ttft_s_median']} | {s['gen_tps_median']} | {s['prompt_tps_median']} | {acc} | {s['tool_latency_s_median']} |"
        )
    lines.append("")
    lines.append("Outcome-Verteilung: " + "; ".join(f"{r.model}: {r.summary()['outcomes']}" for r in reports))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="+")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--speed-runs", type=int, default=3)
    parser.add_argument("--skip-speed", action="store_true")
    parser.add_argument("--skip-tools", action="store_true")
    parser.add_argument("--registry", action="store_true", help="use the live backend tool registry instead of the target catalog")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=None, help="directory for results JSON + Markdown")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    suite = json.loads(args.cases.read_text(encoding="utf-8"))
    tools = load_tools(suite, args.registry)
    ollama = Ollama(args.base_url, args.timeout)

    reports: list[ModelReport] = []
    for model in args.models:
        print(f"=== {model}", flush=True)
        report = run_model(ollama, model, suite, tools, args)
        reports.append(report)
        print(json.dumps(report.summary(), ensure_ascii=False, indent=2), flush=True)

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (args.out / f"llm-{stamp}.json").write_text(
            json.dumps([{"summary": r.summary(), "cases": [asdict(c) for c in r.cases]} for r in reports], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_markdown(reports, args.out / f"llm-{stamp}.md")
        print(f"Results written to {args.out}", flush=True)


if __name__ == "__main__":
    main()
