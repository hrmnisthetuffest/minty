"""Daily provider canary: does each provider still answer, correctly and quickly?

First a free check (the model-list request, no tokens), then up to five tiny fixed prompts per free provider, but
only while at least half of today's known capacity is left and never on a paid provider. A reporting tool: it does
not change routing by itself.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from minty_core.llm.types import LLMError, Message

PROMPTS = [("Reply with exactly the word OK.", "ok"),
           ("What is 2 plus 2? Reply with the digit only.", "4"),
           ("What is the capital of France? One word.", "paris"),
           ("Reply with the word blue and nothing else.", "blue"),
           ("Spell the word cat backwards, letters only.", "tac")]
SLOW_S = 6.0


@dataclass
class ProviderCanary:
    name: str
    probe: str = "not checked"
    passed: int = 0
    total: int = 0
    average_s: float | None = None
    error: str = ""


@dataclass
class CanaryReport:
    results: list[ProviderCanary] = field(default_factory=list)
    skipped: str = ""
    at: float = 0.0

    def words(self) -> str:
        if not self.results:
            return "There is no provider to check."
        parts = []
        for r in self.results:
            if r.probe not in ("ok", "not checked") and not r.total:
                parts.append(f"{r.name} failed its key check ({r.probe})")
            elif r.total:
                slow = f", slow at {r.average_s:.1f} s" if r.average_s and r.average_s >= SLOW_S else ""
                err = f" ({r.error})" if r.error else ""
                parts.append(f"{r.name} {r.passed} of {r.total}{slow}{err}")
            else:
                parts.append(f"{r.name} key ok")
        note = f" I skipped the questions: {self.skipped}." if self.skipped else ""
        return "Provider check: " + "; ".join(parts) + "." + note


async def run_canary(router, *, share: Callable[[], float | None], min_share: float = 0.5,
                     prompts=PROMPTS, state_path: Path | None = None,
                     clock: Callable[[], float] = time.time) -> CanaryReport:
    report = CanaryReport(at=clock())
    try:
        probed = await router.probe()
    except Exception as e:  # noqa: BLE001 — a probe failure is a finding, not a crash
        probed = {}
        report.skipped = f"the key check failed ({type(e).__name__})"
    capacity = share()
    ask = report.skipped == ""
    if ask and capacity is not None and capacity < min_share:
        report.skipped, ask = f"only {round(capacity * 100)} percent of today's capacity is left", False
    for cfg in router.usable():
        result = ProviderCanary(cfg.name, probed.get(cfg.name, "not checked"))
        report.results.append(result)
        if not ask or cfg.is_paid() or result.probe not in ("ok", "not checked"):
            continue
        times = []
        for text, expected in prompts:
            result.total += 1
            try:
                reply, seconds = await router.ask_one(cfg.name, [Message("user", text)], timeout_s=20.0)
            except LLMError as e:
                result.error = e.kind if isinstance(e.kind, str) else "error"
                continue
            times.append(seconds)
            result.passed += expected in reply.lower()
        result.average_s = sum(times) / len(times) if times else None
    if state_path is not None:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        Path(state_path).write_text(json.dumps({
            "at": report.at, "skipped": report.skipped,
            "results": [r.__dict__ for r in report.results]}), encoding="utf-8")
    return report
