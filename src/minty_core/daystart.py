"""The day-start routine: on the first conversation of a new day (after 04:00), once the conversation is over, a
short brief: the day, the weather, today's reminders and what is waiting for your approval.

The scheduled morning brief (at the time you set) counts: if it already ran today this stays quiet.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


class DayStart:
    def __init__(self, state_path: Path, *, compose: Callable[[], Awaitable[str]], notify: Callable[[str], None],
                 pending_approvals: Callable[[], list] = lambda: [], enabled: Callable[[], bool] = lambda: True,
                 after: str = "04:00", clock: Callable[[], datetime] = datetime.now):
        self.state_path = Path(state_path)
        self._compose, self._notify, self._pending = compose, notify, pending_approvals
        self._enabled, self._clock = enabled, clock
        hour, _, minute = after.partition(":")
        self._after = (int(hour), int(minute or 0))

    def _done_day(self) -> str:
        try:
            return self.state_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def mark_done(self, day: date | None = None) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text((day or self._clock().date()).isoformat(), encoding="utf-8")

    def due(self, now: datetime | None = None) -> bool:
        now = now or self._clock()
        return (self._enabled() and (now.hour, now.minute) >= self._after
                and self._done_day() != now.date().isoformat())

    def on_first(self) -> None:
        """Called for every conversation. Cheap; only the first of the day starts anything."""
        if not self.due():
            return
        self.mark_done()  # first, so two quick conversations cannot start two briefs
        try:
            asyncio.get_running_loop().create_task(self._run())
        except RuntimeError:
            log.debug("no running loop for the day-start routine")

    async def _run(self) -> None:
        try:
            text = await self._compose()
            waiting = self._pending()
            if waiting:
                text += f" {len(waiting)} thing{'s are' if len(waiting) != 1 else ' is'} waiting for your approval."
            if text.strip():
                self._notify(text.strip())  # spoken once nothing else is going on
        except Exception:  # noqa: BLE001 — a failed brief must not matter to anything else
            log.warning("day-start routine failed", exc_info=True)
