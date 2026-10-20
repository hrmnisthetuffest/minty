"""One handle on the self-check helpers for the plugin and the loop. Nothing is created on disk until `selfcheck.enabled` is on and
something asks."""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text
from minty_core.selfcheck.card import card_svg, gather, render_png
from minty_core.selfcheck.proposals import Proposals
from minty_core.selfcheck.report import format_report, load_week, weekly_report
from minty_core.selfcheck.tips import TipBook
from minty_core.selfcheck.timeline import build_timeline

OFF = "The self-check helpers are switched off (selfcheck.enabled on the Switches page)."


class SelfcheckOff(RuntimeError):
    pass


class SelfcheckProvider:
    def __init__(self, config: Callable, directory: Path, services: dict, clock: Callable[[], float] = time.time,
                 runner=None, trace_dir: Path | None = None):
        self._config, self._dir, self._services, self._clock, self._runner = config, Path(directory), services, clock, runner
        self._trace_dir = Path(trace_dir) if trace_dir else self._dir / "traces"

    def need(self) -> None:
        if not self._config().selfcheck.enabled:
            raise SelfcheckOff(OFF)

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._clock())

    def proposals(self) -> Proposals:
        return Proposals(self._dir / "selfcheck-proposals.json", self._clock)

    def tips(self) -> TipBook:
        return TipBook(self._dir / "selfcheck-tips.json")

    def apply_instruction(self, text: str) -> None:
        layers = self._services.get("prompts")
        if layers is None:
            raise ValueError("The standing instructions are not available just now.")
        layers.add_instruction(text)

    def report(self) -> tuple[str, int]:
        """(the report text, how many new proposals it made)."""
        today = self.now().date()
        r = weekly_report(load_week(self._trace_dir, today), today)
        made = self.proposals().propose_from(r.clusters)
        waiting = len(self.proposals().list("open"))
        text = format_report(r)
        if waiting:
            text += f"\n{waiting} proposal{'s' * (waiting != 1)} waiting for your yes or no (say 'show the proposals')."
        return text, len(made)

    def timeline(self, limit: int = 12) -> list[dict]:
        runner, store = self._services.get("jobs"), self._services.get("receipts")
        return build_timeline(runner.select()[-60:] if runner is not None else [], store.recent(60) if store is not None else [], limit)

    def tip(self, day: date | None = None) -> str:
        return self.tips().pick(self._config(), day or self.now().date())

    async def card(self, push: bool = False) -> tuple[Path | None, str]:
        """(the PNG, or None, and the SVG path's folder). Sent to the phone only when `push` and selfcheck.card_push are on."""
        day = self.now().date()
        data = gather(self._services, self._config(), day, self.tip(day), self.now())
        folder = self._dir / "cards"
        png = await render_png(card_svg(data), folder, f"card-{day:%Y%m%d}", runner=self._runner)
        if png is not None and push and self._config().selfcheck.card_push:
            publish = self._services.get("publish")
            if publish is not None:
                publish("card", path=str(png), caption=f"Your day, {day:%A %-d %b}")
        return png, str(folder)

    # ---- the loop ---------------------------------------------------------------------------------------
    def _state(self) -> dict:
        try:
            return json.loads((self._dir / "selfcheck-state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember(self, **kv) -> None:
        state = {**self._state(), **kv}
        atomic_write_text(self._dir / "selfcheck-state.json", json.dumps(state), mode=0o600)

    @staticmethod
    def _due(now: datetime, hhmm: str, grace_min: int = 30) -> bool:
        h, m = (int(x) for x in hhmm.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return start <= now <= start + timedelta(minutes=grace_min)

    async def tick(self) -> list[str]:
        cfg = self._config().selfcheck
        if not cfg.enabled:
            return []
        now, today, out = self.now(), self.now().date().isoformat(), []
        state = self._state()
        if cfg.tip_push and state.get("tip") != today and self._due(now, cfg.tip_time):
            self._remember(tip=today)
            out.append("Tip: " + self.tip())
        week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        if cfg.weekly_report and now.weekday() == 6 and now.hour >= 18 and state.get("report") != week:
            self._remember(report=week)
            out.append(self.report()[0])
        if cfg.card_push and state.get("card") != today and self._due(now, cfg.card_time):
            self._remember(card=today)
            await self.card(push=True)
        return out
