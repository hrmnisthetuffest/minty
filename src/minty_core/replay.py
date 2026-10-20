"""Replay a recorded day: the day's requests (from the conversation archive) are sent again through the current
build, and what happens is compared with what happened that day (from the traces).

Fake mode answers with a stand-in model, so it costs nothing: it measures what the build would send (characters,
tools offered, model calls, time to the first sentence) on your real requests. Real mode uses your providers and
spends free quota. Private turns are left out unless asked for, and --scrub hides contact details and names in
the report.
"""
from __future__ import annotations

import json
import shutil
import statistics
import time
from pathlib import Path

from minty_core.llm.types import TextDelta
from minty_core.pseudonym import Pseudonymizer
from minty_core.trace import read_events, turn_scope


def load_day(config_dir: Path, day: str, *, include_private: bool = False, scrub: bool = False,
             names=()) -> list[dict]:
    raw = Path(config_dir) / "history" / "raw" / f"{day}.jsonl"
    turns = {e["turn_id"]: e for e in read_events(Path(config_dir) / "traces", day)
             if e.get("kind") == "turn" and e.get("turn_id")}
    masker = Pseudonymizer(names) if scrub else None
    out = []
    for line in raw.read_text(encoding="utf-8").splitlines() if raw.exists() else []:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("role") != "user" or not str(row.get("text", "")).strip():
            continue
        if row.get("private") and not include_private:
            continue
        before = turns.get(row.get("turn_id"))
        out.append({"text": masker.mask(row["text"]) if masker else row["text"],
                    "before": {"outcome": before.get("outcome"), "first_audio_ms": before.get("first_audio_ms")}
                    if before else None})
    return out


class _StandIn:
    """A stand-in model: answers every request with one short sentence and never calls a tool."""

    def __init__(self, name: str):
        self.name = name

    async def stream(self, messages, tools):
        yield TextDelta("Replayed.")


def _commands(ctx):
    from minty_core.commands import CommandRouter
    s = ctx.services
    return CommandRouter(ctx.agent, ctx.store, prompts=s.get("prompts"), router=ctx.router, jobs=s.get("jobs"),
                         capacity=s.get("brownout"), where=s.get("where"), receipt=s.get("receipt"))


async def _run(ctx, turns: list[dict]) -> list[dict]:
    commands = _commands(ctx)
    tracer = ctx.services["tracer"]
    rows = []
    for t in turns:
        start, first, reply, outcome = time.perf_counter(), None, "", "answered"
        with turn_scope() as turn:
            fixed = commands.handle(t["text"])
            if fixed is not None:
                reply, outcome, first = fixed, "command", time.perf_counter()
            else:
                try:
                    parts = []
                    async for sentence in ctx.agent.respond(t["text"]):
                        first = first or time.perf_counter()
                        parts.append(sentence)
                    reply = " ".join(parts)
                except Exception as e:  # noqa: BLE001 — a failure is a result to report, not a crash
                    reply, outcome = f"{type(e).__name__}: {e}", "error"
        tracer.flush()
        events = [e for e in read_events(tracer.directory) if e.get("turn_id") == turn["turn_id"]]
        calls = [e for e in events if e.get("kind") == "llm_call"]
        rows.append({"text": t["text"], "reply": reply[:300], "outcome": outcome, "before": t.get("before"),
                     "instant": any(e.get("kind") == "instant" for e in events),  # answered without a model (docs/instant-answers.md)
                     "first_sentence_ms": round((first - start) * 1000, 1) if first else None,
                     "llm_calls": len(calls), "chars_sent": sum(int(e.get("chars_in") or 0) for e in calls),
                     "tools_offered": max((int(e.get("tools") or 0) for e in calls), default=0),
                     "tokens_in": sum(int(e.get("tokens_in") or 0) for e in calls)})
    return rows


async def replay_fake(turns: list[dict], work_dir: Path) -> list[dict]:
    """Run the requests through a fresh build with a stand-in model: nothing leaves the machine."""
    from minty_core.context import build_context

    ctx = build_context(Path(work_dir))
    data = ctx.store.config.model_dump(mode="json")
    data["providers"] = [{"name": "stand-in", "model": "replay", "requires_key": False}]
    ctx.store.update(data)
    ctx.router._factory = lambda cfg, key: _StandIn(cfg.name)
    return await _run(ctx, turns)


async def replay_real(turns: list[dict], config_dir: Path, work_dir: Path) -> list[dict]:
    """Run the requests with your real config and keys (spends free quota; the real ledger counts it). The
    conversation itself happens in a scratch copy, so your history is not changed."""
    from minty_core.context import build_context
    from minty_core.llm.ledger import CapacityLedger

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    work.chmod(0o700)
    for name in ("config.toml", "secrets.toml"):
        if (Path(config_dir) / name).exists():
            shutil.copy2(Path(config_dir) / name, work / name)
    ctx = build_context(work)
    ctx.router.ledger = CapacityLedger(Path(config_dir) / "capacity.sqlite")
    return await _run(ctx, turns)


def _pct(values: list[float], q: float) -> float | None:
    values = sorted(v for v in values if v is not None)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else None


def summarise_replay(rows: list[dict]) -> dict:
    before = [r["before"]["first_audio_ms"] for r in rows if r.get("before") and r["before"].get("first_audio_ms")]
    return {
        "requests": len(rows),
        "answered": sum(1 for r in rows if r["outcome"] in ("answered", "command")),
        "errors": sum(1 for r in rows if r["outcome"] == "error"),
        "first_sentence_p50_ms": _pct([r["first_sentence_ms"] for r in rows], 0.5),
        "first_sentence_p90_ms": _pct([r["first_sentence_ms"] for r in rows], 0.9),
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "chars_sent_median": statistics.median([r["chars_sent"] for r in rows if r["llm_calls"]] or [0]),
        "tools_offered_median": statistics.median([r["tools_offered"] for r in rows if r["llm_calls"]] or [0]),
        "before_outcomes": {o: sum(1 for r in rows if (r.get("before") or {}).get("outcome") == o)
                            for o in {(r.get("before") or {}).get("outcome") for r in rows} if o},
        "before_first_audio_p50_ms": _pct(before, 0.5),
    }
