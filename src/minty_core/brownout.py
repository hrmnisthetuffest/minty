"""Brownout, capacity meter and forecast: how much of today's free model capacity is left, in calls, and what
changes when it runs low (shorter answers, no optional model work, one spoken notice a day).

Only providers with known daily limits count (Groq, OpenRouter's free models); NVIDIA, Mistral and Gemini publish
no daily numbers, so they are left out rather than guessed. Days are UTC, as the providers count them.
"""
from __future__ import annotations

import calendar
import time
from dataclasses import dataclass
from typing import Callable

from minty_core.timefmt import clock12

AVG_TOKENS_PER_CALL = 6000  # 2026-10-11 median (history + tools) until the ledger has today's own average


@dataclass
class Capacity:
    share: float | None  # 0..1 of today's known capacity left
    calls_left: int | None
    calls_total: int | None
    forecast: str  # "lasts until the daily reset" / "runs out around 15:10" / ""


def capacity_reply(cap: Capacity) -> str:
    if cap.share is None:
        return "I can't tell: none of the models I'm using has a known daily limit."
    return (f"About {round(cap.share * 100)} percent of today's free capacity is left, roughly {cap.calls_left} "
            f"calls. At this pace it {cap.forecast}.")


class Brownout:
    def __init__(self, router, ledger, store, *, clock: Callable[[], float] = time.time,
                 notify: Callable[[str], None] | None = None):
        self._router, self._ledger, self._store, self._clock = router, ledger, store, clock
        self._notify = notify or (lambda text: None)
        self._noticed_day = ""

    def _models(self):
        for cfg in self._router.usable():
            if cfg.tier != "fast":
                continue
            yield cfg
            small = cfg.effective_small_model()
            if small and small != cfg.model:
                yield cfg.model_copy(update={"model": small})

    def capacity(self) -> Capacity:
        total = left = used = 0
        known = False
        for cfg in self._models():
            limits = cfg.effective_limits()
            spent = self._ledger.used(cfg.name, cfg.model)
            avg = spent["tokens"] / spent["requests"] if spent["requests"] else AVG_TOKENS_PER_CALL
            caps = [c for c in (limits.rpd, int(limits.tpd // avg) if limits.tpd else None) if c is not None]
            if not caps:
                continue
            known = True
            remaining = self._ledger.remaining(cfg)
            lefts = [c for c in (remaining["requests"],
                                 int(remaining["tokens"] // avg) if remaining["tokens"] is not None else None)
                     if c is not None]
            total += min(caps)
            left += min(lefts) if lefts else min(caps)
            used += spent["requests"]
        if not known or total == 0:
            return Capacity(None, None, None, "")
        return Capacity(round(left / total, 3), left, total, self._forecast(left, used))

    def _forecast(self, left: int, used: int) -> str:
        now = self._clock()
        day_start = calendar.timegm(time.gmtime(now)[:3] + (0, 0, 0))
        hours = max(1.0, (now - day_start) / 3600)
        rate = used / hours
        if rate <= 0:
            return "lasts until the daily reset"
        runs_out = now + left / rate * 3600
        if runs_out >= day_start + 86400:
            return "lasts until the daily reset"
        return "runs out around " + clock12(runs_out)

    def active(self) -> bool:
        cfg = self._store.config.router
        if not cfg.brownout:
            return False
        share = self.capacity().share
        return share is not None and share < cfg.brownout_below

    def prompt(self) -> str:
        """A system-prompt line while capacity is low."""
        if not self.active():
            return ""
        return ("\n\nFree model capacity is low today: answer in one or two short sentences and skip optional "
                "extras until it resets.")

    def check(self) -> None:
        """After a turn: say once a day that capacity is low."""
        day = time.strftime("%Y-%m-%d", time.gmtime(self._clock()))
        if self._noticed_day == day or not self.active():
            return
        self._noticed_day = day
        below = round(self._store.config.router.brownout_below * 100)
        self._notify(f"Heads up: less than {below} percent of today's free model capacity is left, so I'll keep "
                     "answers short until it resets.")
