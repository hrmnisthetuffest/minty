"""Focus mode (745) and the distraction guard (603).

Focus is a bundle, not a lock: while it runs, non-urgent spoken notices wait and come back as one line when it ends,
and (with the guard) a gentle question is asked if a distracting window stays in front. It never blocks anything."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from minty_core.overlay.xwin import ActiveWindow


class Focus:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def start(self, minutes: float, label: str = "focus") -> float:
        until = self._clock() + max(1.0, min(float(minutes), 480.0)) * 60
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"until": until, "label": (label or "focus")[:60]}, f)
        return until

    def stop(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    @property
    def label(self) -> str:
        return str(self._read().get("label", "focus"))

    def remaining(self) -> float:
        return max(0.0, float(self._read().get("until", 0)) - self._clock())

    def active(self) -> bool:
        return self.remaining() > 0

    def holds(self, event: dict) -> bool:
        """Whether a spoken notice should wait: focus is on and the notice is not marked urgent."""
        return self.active() and not event.get("urgent")


class DistractionGuard:
    def __init__(self, focus: Focus, words: list[str], clock: Callable[[], float] = time.time, grace_s: float = 20.0,
                 every_s: float = 600.0):
        self.focus, self.words = focus, [w.lower() for w in words]
        self._clock, self.grace_s, self.every_s = clock, grace_s, every_s
        self._since: tuple[str, float] | None = None
        self._asked_at: float | None = None

    def check(self, window: ActiveWindow | None) -> str | None:
        """A question to ask once the distraction has lasted `grace_s` (at most every `every_s`), or None."""
        if window is None or not self.focus.active():
            self._since = None
            return None
        haystack = f"{window.title} {window.wm_class}".lower()
        hit = next((w for w in self.words if w in haystack), None)
        if hit is None:
            self._since = None
            return None
        now = self._clock()
        if self._since is None or self._since[0] != hit:
            self._since = (hit, now)
            return None
        if now - self._since[1] < self.grace_s:
            return None
        if self._asked_at is not None and now - self._asked_at < self.every_s:
            return None
        self._asked_at = now
        i = haystack.find(hit)
        shown = window.title[i:i + len(hit)] if 0 <= i < len(window.title) and window.title[i:i + len(hit)].lower() \
            == hit else hit.title()
        return (f"That's {shown}. You're in focus time for {self.focus.label}; want to carry on with it? "
                "Say end focus if you're done.")
