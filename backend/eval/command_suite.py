"""
German command suite against the REAL conversation engine.

Unlike eval/bench_llm.py (raw model + a fixed benchmark prompt), this runs
every case through app/agent/engine.py exactly as voice/chat do: production
system prompt, live tool registry, quick commands and the action guard.
Tools are NOT executed - a dry-run executor returns plausible fake results,
so nothing gets paused, typed or opened on this PC while the suite runs.

    python -m eval.command_suite                         # configured model
    python -m eval.command_suite --model qwen3.5:9b --model qwen3.5:4b --repeat 2
    python -m eval.command_suite --only note,timer -v

Writes docs/overhaul/bench/commands-<model>.{json,md}. Re-run after every
change to app/persona.py or tool descriptions - small prompt changes can
swing the tool hit rate by 50 points (see the M3 report).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.action_guard import NOT_DONE_REPLY, unverified_action_reply  # noqa: E402
from app.agent.engine import ApprovalRequired, ConversationEngine, ConversationStore, Sentence, ToolFinished, ToolStarted, TurnDone, TurnFailed  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.log import setup_logging  # noqa: E402
from app.llm.factory import build_local_provider  # noqa: E402
from app.llm.ollama import OllamaAdmin, model_matches  # noqa: E402
from app.tools.base import ToolResult  # noqa: E402
from app.tools.catalog import build_registry, exposed_tool_names  # noqa: E402
from app.tools.executor import ToolExecutor  # noqa: E402

DEFAULT_CASES = Path(__file__).with_name("commands_de.json")
DEFAULT_OUT = ROOT.parent / "docs" / "overhaul" / "bench"

FAKE_RESULTS: Dict[str, Any] = {
    "get_weather": {"location": "Zürich", "day": "tomorrow", "conditions": "Regen", "temp_max_c": 15.1, "temp_min_c": 9.4, "precipitation_probability": 80},
    "get_volume": {"level": 40, "muted": False},
    "set_volume": {"level": 30, "muted": False},
    "media_control": {"done": True},
    "get_now_playing": {"title": "Bohemian Rhapsody", "artist": "Queen", "app": "Spotify", "playing": True},
    "read_clipboard": {"text": "Einkaufsliste: Milch, Brot"},
    "write_clipboard": {"chars": 10},
    "list_notes": {"count": 1, "notes": [{"day": "heute", "heading": "09:12", "text": "Milch kaufen"}]},
    "list_timers": {"timers": [{"label": "Pizza", "remaining_seconds": 300}]},
    "cancel_timer": {"cancelled": 1},
    "set_timer": {"timer_id": "t1", "due_at": "in Kürze"},
    "web_search": {"results": [{"title": "Migros Bern Bahnhof", "snippet": "Mo-Sa 6-22 Uhr", "url": "https://example.org/migros"}]},
    "fetch_url": {"url": "https://example.com", "text": "Example Domain. This domain is for use in illustrative examples in documents."},
    "find_files": {"results": [{"path": "C:/Users/Silvan/Documents/Bewerbung_2025.pdf"}]},
    "get_system_info": {"cpu_percent": 12, "ram_free_gb": 20.5, "ram_total_gb": 32},
    "describe_screen": {"description": "Ein Code-Editor mit Python-Code ist geöffnet."},
    "open_app": {"opened": True},
    "get_current_time": {"time": "12:00"},
}
UNTRUSTED = {"web_search", "fetch_url"}


@dataclass
class CaseRun:
    id: str
    prompt: str
    expect: Optional[str]
    tools: List[str] = field(default_factory=list)
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_args: List[Dict[str, Any]] = field(default_factory=list)
    reply: str = ""
    tool_ok: bool = False
    args_ok: Optional[bool] = None
    reply_ok: Optional[bool] = None
    length_ok: Optional[bool] = None
    hallucinated: bool = False
    guard_retries: int = 0
    quick: bool = False
    failed: Optional[str] = None
    first_sentence_s: Optional[float] = None
    total_s: Optional[float] = None


def install_dry_run() -> None:
    async def execute(self, name, arguments, db=None, ctx=None, *, approval_id=None):  # noqa: ANN001
        plan = self.plan(name, arguments, ctx)
        if plan.error:
            return ToolResult(success=False, error=plan.error)
        return ToolResult(success=True, data=FAKE_RESULTS.get(name, {"ok": True}), untrusted=name in UNTRUSTED)

    async def record_decision(self, *args, **kwargs):  # noqa: ANN001 - keep the real audit log clean
        return None

    ToolExecutor.execute = execute  # type: ignore[method-assign]
    ToolExecutor.record_decision = record_decision  # type: ignore[method-assign]


def _value_ok(got: Any, accepted: List[Any]) -> bool:
    for want in accepted:
        if isinstance(want, bool) or isinstance(got, bool):
            if got is want:
                return True
        elif isinstance(want, (int, float)):
            if isinstance(got, (int, float)) and got == want:
                return True
        elif isinstance(got, str) and str(want).lower() in got.lower():
            return True
    return False


def evaluate(case: Dict[str, Any], run: CaseRun, registry) -> None:  # noqa: ANN001
    accepted = ([case["expect"]] if case["expect"] else []) + case.get("alt", [])
    if case["expect"] is None:
        run.tool_ok = not run.tools or run.tools[0] in accepted
    else:
        # A look-up first is fine ("list_timers" before "cancel_timer").
        hit = next((i for i, name in enumerate(run.tools) if name in accepted), None)
        run.tool_ok = hit is not None
        # The guard's honest "nicht ausgeführt" is a miss, not a false claim.
        run.hallucinated = not run.tools and run.reply != NOT_DONE_REPLY and unverified_action_reply(run.reply)
        if hit is not None and hit < len(run.call_args):
            run.arguments = run.call_args[hit]
    if run.tool_ok and run.tools and case.get("args"):
        tool = registry.get(next(n for n in run.tools if n in accepted))
        known = set(((tool.parameters or {}).get("properties") or {}).keys()) if tool else set()
        checks = [(k, v) for k, v in case["args"].items() if k in known]
        run.args_ok = all(k in run.arguments and _value_ok(run.arguments[k], v) for k, v in checks)
    if case.get("reply_contains"):
        run.reply_ok = any(s.lower() in run.reply.lower() for s in case["reply_contains"])
    if case.get("reply_max_chars"):
        run.length_ok = len(run.reply) <= case["reply_max_chars"]
    if case.get("reply_min_chars"):
        run.length_ok = len(run.reply) >= case["reply_min_chars"]


async def run_case(engine: ConversationEngine, case: Dict[str, Any]) -> CaseRun:
    run = CaseRun(case["id"], case["prompt"], case["expect"])
    sentences: List[str] = []
    started = time.perf_counter()
    async for event in engine.run_turn(uuid.uuid4().hex, case["prompt"], source="voice"):
        if isinstance(event, ToolStarted):
            run.tools.append(event.name)
        elif isinstance(event, ToolFinished):
            run.call_args.append(dict(event.arguments))
            run.arguments = run.arguments or dict(event.arguments)
        elif isinstance(event, ApprovalRequired):
            run.tools.append(event.tool_name)
            run.call_args.append(dict(event.arguments))
            run.arguments = run.arguments or dict(event.arguments)
            run.reply = event.spoken_prompt
            run.total_s = round(time.perf_counter() - started, 3)
        elif isinstance(event, Sentence):
            sentences.append(event.text)
            if run.first_sentence_s is None:
                run.first_sentence_s = round(time.perf_counter() - started, 3)
        elif isinstance(event, TurnDone):
            run.reply = event.reply or " ".join(sentences)
            run.guard_retries = int(event.metrics.get("guard_retries", 0))
            run.quick = bool(event.metrics.get("quick_command"))
            run.total_s = round(time.perf_counter() - started, 3)
        elif isinstance(event, TurnFailed):
            run.failed = event.error.message
    return run


async def prepare_model(settings, model: str) -> None:  # noqa: ANN001
    """Single-model policy: two resident models spill into shared memory on
    Windows and make everything 10-20x slower (M0 finding)."""
    admin = OllamaAdmin(settings.LOCAL_LLM_BASE_URL)
    for loaded in await admin.loaded_models():
        name = loaded.get("name") or loaded.get("model") or ""
        if not model_matches(name, model):
            await admin.unload(name)


def summarize(model: str, runs: List[CaseRun]) -> Dict[str, Any]:
    action = [r for r in runs if r.expect]
    plain = [r for r in runs if not r.expect]
    with_args = [r for r in runs if r.args_ok is not None]
    with_reply = [r for r in runs if r.reply_ok is not None]
    with_length = [r for r in runs if r.length_ok is not None]
    plain_replies = [len(r.reply) for r in runs if not r.expect and r.reply]
    llm_runs = [r for r in runs if not r.quick and r.first_sentence_s is not None]

    def median(values: List[Optional[float]]) -> Optional[float]:
        vals = [v for v in values if v is not None]
        return round(statistics.median(vals), 2) if vals else None

    return {
        "model": model,
        "cases": len(runs),
        "tool_hits": f"{sum(r.tool_ok for r in action)}/{len(action)}",
        "tool_hit_rate": round(sum(r.tool_ok for r in action) / len(action), 3) if action else None,
        "no_tool_ok": f"{sum(r.tool_ok for r in plain)}/{len(plain)}",
        "args_ok": f"{sum(bool(r.args_ok) for r in with_args)}/{len(with_args)}",
        "reply_ok": f"{sum(bool(r.reply_ok) for r in with_reply)}/{len(with_reply)}",
        "length_ok": f"{sum(bool(r.length_ok) for r in with_length)}/{len(with_length)}",
        "median_reply_chars_no_tool": int(statistics.median(plain_replies)) if plain_replies else None,
        "hallucinated": sum(r.hallucinated for r in runs),
        "guard_retries": sum(r.guard_retries for r in runs),
        "guard_saves": sum(1 for r in runs if r.guard_retries and r.tool_ok and r.expect),
        "quick_commands": sum(r.quick for r in runs),
        "failed": sum(1 for r in runs if r.failed),
        "median_first_sentence_s": median([r.first_sentence_s for r in llm_runs]),
        "median_total_s": median([r.total_s for r in runs if not r.quick]),
    }


def to_markdown(summary: Dict[str, Any], runs: List[CaseRun]) -> str:
    lines = [
        f"# Befehls-Suite: {summary['model']}",
        "",
        f"Erzeugt mit `python -m eval.command_suite` am {time.strftime('%Y-%m-%d %H:%M')} (Dry-Run, echte Engine).",
        "",
        "| Kennzahl | Wert |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in summary.items() if k != "model"]
    lines += ["", "| Fall | Prompt | erwartet | bekommen | Tool | Args | Antwort | Länge | Zeit (s) | Antworttext |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        mark = lambda v: "–" if v is None else ("✅" if v else "❌")  # noqa: E731
        got = ", ".join(r.tools) or "–"
        extra = (" (Schnellbefehl)" if r.quick else "") + (f" (Wächter ×{r.guard_retries})" if r.guard_retries else "") + (" ⚠️ behauptet" if r.hallucinated else "")
        reply = r.reply.replace("|", "/").replace("\n", " ")[:90]
        lines.append(f"| {r.id} | {r.prompt} | {r.expect or '–'} | {got}{extra} | {mark(r.tool_ok)} | {mark(r.args_ok)} | {mark(r.reply_ok)} | {mark(r.length_ok)} {len(r.reply)} | {r.total_s} | {reply} |")
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", help="model(s) to test (default: LOCAL_LLM_MODEL)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    base = get_settings()
    setup_logging(base)
    install_dry_run()
    registry = build_registry()
    exposed = set(exposed_tool_names(base, registry))
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    from app.persona import system_prompt  # noqa: PLC0415

    prompt = system_prompt("voice")
    skipped = [
        c["id"] for c in cases
        if (c.get("needs_tool") and c["needs_tool"] not in exposed) or (c.get("requires_prompt") and c["requires_prompt"] not in prompt)
    ]
    cases = [c for c in cases if c["id"] not in skipped]
    if skipped:
        print(f"Übersprungen (Tool fehlt oder Gedächtnis-Fakt nicht im Prompt): {', '.join(skipped)}")

    models = args.model or [base.LOCAL_LLM_MODEL]
    for model in models:
        settings = base.model_copy(update={"LOCAL_LLM_MODEL": model})
        await prepare_model(settings, model)
        llm = build_local_provider(settings)
        runs: List[CaseRun] = []
        await run_case(ConversationEngine(llm, registry, settings=settings, store=ConversationStore()), {"id": "warmup", "prompt": "Hallo", "expect": None})
        for _ in range(args.repeat):
            for case in cases:
                engine = ConversationEngine(llm, registry, settings=settings, store=ConversationStore())
                run = await run_case(engine, case)
                evaluate(case, run, registry)
                runs.append(run)
                if args.verbose or not run.tool_ok or run.args_ok is False or run.reply_ok is False or run.length_ok is False:
                    status = "OK" if run.tool_ok and run.args_ok is not False and run.reply_ok is not False and run.length_ok is not False else "XX"
                    print(f"  {status} {run.id:14} tools={run.tools} args={json.dumps(run.arguments, ensure_ascii=False)[:70]} reply={run.reply[:70]!r}", flush=True)
        summary = summarize(model, runs)
        print(json.dumps(summary, ensure_ascii=False))
        if not args.no_save:
            args.out.mkdir(parents=True, exist_ok=True)
            stem = "commands-" + model.replace(":", "-").replace("/", "-")
            (args.out / f"{stem}.json").write_text(json.dumps({"summary": summary, "runs": [asdict(r) for r in runs]}, ensure_ascii=False, indent=2), encoding="utf-8")
            (args.out / f"{stem}.md").write_text(to_markdown(summary, runs), encoding="utf-8")
    if models != [base.LOCAL_LLM_MODEL]:
        # Leave the PC as we found it: only the default model resident.
        await prepare_model(base, base.LOCAL_LLM_MODEL)


if __name__ == "__main__":
    asyncio.run(main())
