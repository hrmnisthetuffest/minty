"""A small live summary of the event bus for the overlay: the state, what is being heard or said, the latest notice
with how old each is, and the latest notes while noting mode is on. The control API serves it; the HUD polls it."""
from __future__ import annotations

import time
from typing import Callable


class OverlayFeed:
    def __init__(self, bus, clock: Callable[[], float] = time.monotonic):
        self._bus, self._clock = bus, clock
        self._queue = bus.subscribe({"state", "partial", "transcript", "notice", "error", "notes"})
        self._state = "idle"
        self._partial, self._tentative = "", ""
        self._sentence, self._sentence_at = "", None
        self._notice, self._notice_at = "", None
        self._noting, self._notes = False, []

    async def run(self) -> None:
        while True:
            event = await self._queue.get()
            kind, now = event.get("type"), self._clock()
            if kind == "state":
                self._state = str(event.get("state", "idle"))
            elif kind == "partial":
                self._partial, self._tentative = str(event.get("text", "")), str(event.get("tentative", ""))
            elif kind == "transcript" and event.get("role") == "assistant":
                self._sentence, self._sentence_at = str(event.get("text", "")), now
            elif kind == "notes":
                self._noting = bool(event.get("active"))
                self._notes = [str(x) for x in event.get("items", [])] if self._noting else []
            elif kind in ("notice", "error"):
                self._notice, self._notice_at = str(event.get("message", "")), now

    def snapshot(self) -> dict:
        now = self._clock()
        age = lambda t: round(now - t, 1) if t is not None else 1e6  # noqa: E731
        return {"state": self._state, "partial": self._partial, "tentative": self._tentative,
                "sentence": self._sentence, "sentence_age_s": age(self._sentence_at),
                "notice": self._notice, "notice_age_s": age(self._notice_at),
                "noting": self._noting, "notes": list(self._notes)}
