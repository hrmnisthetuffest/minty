"""Lockdown: one phrase stops everything and closes the doors until it is explicitly lifted.

While it is on, nothing that changes anything or reaches the network runs, jobs are stopped, waiting approvals are
refused, scheduled work does not start and remote access (Telegram, later) is revoked through the hooks. It is kept
on disk, so a restart does not lift it. Only an explicit release does: the unlock phrase by voice, the terminal, or
the API with the token. A chat message can start it, never end it.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# the only tools that still run: local, read-only, and the ones to look at or stop work
ALLOWED_PREFIXES = ("clock_", "memory_recall", "timers_", "jobs_status", "jobs_cancel", "lockdown_")


def allowed_during_lockdown(tool_name: str) -> bool:
    return tool_name.startswith(ALLOWED_PREFIXES)


class Lockdown:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self.on_engage: list[Callable[[], object]] = []  # stop jobs, deny approvals, revoke remote access
        self.on_release: list[Callable[[], object]] = []
        self.active, self.reason, self.since = False, "", 0.0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.active = bool(data.get("active"))
            self.reason, self.since = str(data.get("reason") or ""), float(data.get("since") or 0)
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"active": self.active, "reason": self.reason, "since": self.since}, f)
        tmp.replace(self.path)

    def engage(self, reason: str = "") -> bool:
        """True when this call turned it on (False: it already was)."""
        if self.active:
            return False
        self.active, self.reason, self.since = True, reason, self._clock()
        self._save()  # first: a crash while stopping things must still leave it on
        self._run(self.on_engage)
        return True

    def release(self, via: str = "") -> bool:
        if not self.active:
            return False
        self.active, self.reason = False, ""
        self._save()
        self._run(self.on_release)
        log.info("lockdown lifted via %s", via or "?")
        return True

    @staticmethod
    def _run(hooks: list[Callable[[], object]]) -> None:
        for hook in hooks:
            try:
                hook()
            except Exception:  # noqa: BLE001 — one failing hook must not leave the rest running
                log.warning("a lockdown hook failed", exc_info=True)

    def state(self) -> dict:
        return {"active": self.active, "reason": self.reason, "since": self.since}
