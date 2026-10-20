"""Safe-mode boot: after a crash loop Minty starts with a minimal set of plugins and says so.

Every start is written down as "not yet exited cleanly" and marked clean on a normal shutdown. Three starts in ten
minutes with no clean exit between them means something in the usual set of plugins is taking the program down.
Safe mode is for that run only; nothing in the saved settings changes.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Callable

WINDOW_S = 600.0
THRESHOLD = 3
SAFE_PLUGINS = ("clock", "timers", "memory", "display", "weather", "web_search", "jobs", "prompts")


def is_deliberate_stop(exc: BaseException | None) -> bool:
    """A normal return, Ctrl+C, a service stop or sys.exit(0) is you stopping Minty, not a crash. Waiting for the
    slow clean-up before marking it left a second Ctrl+C (or a kill) to make a stop look like a crash."""
    if exc is None or isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
        return True
    return isinstance(exc, SystemExit) and exc.code in (None, 0)


def start_in_safe_mode(*, crash_loop: bool, auto: bool, requested: bool) -> bool:
    """Safe mode when you asked for it (`minty --safe`), or on a detected crash loop only if you allowed that
    (`ops.auto_safe_mode`, off). Closing the window or killing the process is how Minty is normally stopped, so
    a run of unclean starts alone proves nothing."""
    return requested or (auto and crash_loop)


class BootLog:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time, window_s: float = WINDOW_S,
                 threshold: int = THRESHOLD):
        self.path, self._clock, self._window, self._threshold = Path(path), clock, window_s, threshold

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [e for e in data if isinstance(e, dict) and "ts" in e]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries[-200:], f)  # a week or two of starts, for the health digest
        tmp.replace(self.path)

    def start(self) -> bool:
        """Record this start. True when it should be a safe-mode start."""
        now = self._clock()
        entries = self._load()
        recent = [e for e in entries if now - float(e["ts"]) <= self._window]
        unclean = 0
        for e in reversed(recent):  # newest first, until a clean exit
            if e.get("clean"):
                break
            unclean += 1
        entries.append({"ts": now, "clean": False})
        self._save(entries)
        return unclean + 1 >= self._threshold

    def stats(self, days: int = 7) -> tuple[int, int]:
        """(starts, unclean starts) in the last `days` days, not counting the run in progress."""
        now = self._clock()
        entries = [e for e in self._load() if now - float(e["ts"]) <= days * 86400]
        previous = entries[:-1]
        return len(entries), sum(1 for e in previous if not e.get("clean"))

    def clean_exit(self) -> None:
        entries = self._load()
        if entries:
            entries[-1]["clean"] = True
            self._save(entries)

    def clear(self) -> None:
        self._save([])

    def request_safe(self) -> None:
        """You said "enter safe mode": the next start (only that one) is a safe one."""
        self.path.with_name("safe-next").write_text("1", encoding="utf-8")

    def take_safe_request(self) -> bool:
        marker = self.path.with_name("safe-next")
        try:
            marker.unlink()
            return True
        except OSError:
            return False
