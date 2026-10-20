"""A circuit breaker for tools (docs/performance-plan.md, D4). A tool that fails several times in a row is not offered to the model
for a while: every failed call costs a whole model round trip (seconds), and the model keeps trying because it is offered. After the
rest it gets one more try; if that fails it rests again at once, and a success clears the count."""
from __future__ import annotations

import time
from typing import Callable


class ToolBreaker:
    def __init__(self, threshold: int = 3, rest_s: float = 600.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.rest_s, self._clock = threshold, rest_s, clock
        self._fails: dict[str, int] = {}
        self._until: dict[str, float] = {}

    def record(self, name: str, failed: bool) -> bool:
        """Note one result. True when this one made the tool rest."""
        if not failed:
            self._fails.pop(name, None)
            self._until.pop(name, None)
            return False
        self._fails[name] = self._fails.get(name, 0) + 1
        if self._fails[name] >= self.threshold:
            self._until[name] = self._clock() + self.rest_s
            return True
        return False

    def resting(self) -> set[str]:
        """The tools that should not be offered now. A rested tool is offered again, with the count one short of tripping so
        a single failure puts it back to rest."""
        now = self._clock()
        out = set()
        for name, until in list(self._until.items()):
            if until > now:
                out.add(name)
            else:
                del self._until[name]
                self._fails[name] = self.threshold - 1  # one more failure and it rests again
        return out

    def note(self) -> str:
        names = sorted(self.resting())
        if not names:
            return ""
        return ("These tools keep failing and are switched off for a few minutes: " + ", ".join(names)
                + ". If asked for what they do, say they are unavailable right now; do not try them.")
