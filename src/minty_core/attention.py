"""Open loops: things that need the user, found without being asked and said rarely.

Sources (blocked missions, e-mails that may not have been sent, overdue items, follow-ups owed) each list the loops they can
see. A loop is remembered by a stable key, so it is said at most three times (after a short grace period for the less urgent
ones, then at a cooldown), never more than a few a day in all, and not at all once the user dismisses or snoozes it. When it
resolves, it vanishes, and if the same key comes back later it is a new occasion. The words themselves go out through
`nudge`, which in the app is the scheduler's own gate: night hours, the hourly limit and the off switches still apply.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

H = 3600.0
URGENCY = {"now": 0, "today": 1, "later": 2}
GRACE_S = {"now": 0.0, "today": 2 * H, "later": 24 * H}  # how long a loop is open before it is first said
COOLDOWN_S = {"now": 6 * H, "today": 12 * H, "later": 24 * H}  # between two mentions of the same loop
MAX_NUDGES = 3
TEXT_CHARS = 240


@dataclass(frozen=True)
class Loop:
    key: str  # stable identity of this occasion, e.g. "mission:3:blocked"
    kind: str  # mission | outbox | todo | waiting | mail ...
    text: str  # one sentence that can be said as it is
    since: float  # when it started to need attention
    urgency: str = "today"  # now | today | later


def age_words(seconds: float) -> str:
    if seconds < H:
        return f"{max(1, int(seconds // 60))} minutes"
    if seconds < 48 * H:
        hours = int(seconds // H)
        return f"{hours} hour{'s' * (hours != 1)}"
    return f"{int(seconds // (24 * H))} days"


class Attention:
    def __init__(self, path: Path, sources: dict[str, Callable[[], list[Loop]]], *, nudge: Callable[[str], object],
                 clock: Callable[[], float] = time.time, max_per_day: int = 5):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sources, self._nudge, self._clock, self._max_per_day = sources, nudge, clock, max_per_day
        self._lock = threading.Lock()
        self.problems: dict[str, str] = {}  # source name -> the error it last raised
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS loops (key TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL,"
            " urgency TEXT NOT NULL, since REAL NOT NULL, first_seen REAL NOT NULL, last_nudged REAL,"
            " nudges INTEGER NOT NULL DEFAULT 0, snoozed_until REAL NOT NULL DEFAULT 0, dismissed INTEGER NOT NULL DEFAULT 0)")
        self._db.execute("CREATE TABLE IF NOT EXISTS nudge_log (at REAL NOT NULL)")
        self._db.commit()

    def rebind(self, sources: dict[str, Callable[[], list[Loop]]], nudge: Callable[[str], object]) -> None:
        """After a plugin reload: the same open store, pointed at the new sources and the new way to speak."""
        self._sources, self._nudge = sources, nudge

    # ---- looking ---------------------------------------------------------------------------------
    def _gather(self) -> list[Loop]:
        found: dict[str, Loop] = {}
        for name, source in self._sources.items():
            try:
                for loop in source():
                    found.setdefault(loop.key, loop)
                self.problems.pop(name, None)
            except Exception as e:  # noqa: BLE001 — one broken source must not silence the others
                self.problems[name] = type(e).__name__
                log.warning("attention source %s failed: %s", name, type(e).__name__)
        return list(found.values())

    def _observe(self, loops: list[Loop], now: float) -> None:
        with self._lock:
            keys = [l.key for l in loops]
            marks = ",".join("?" for _ in keys)
            self._db.execute(f"DELETE FROM loops WHERE key NOT IN ({marks})" if keys else "DELETE FROM loops", keys)
            for l in loops:
                self._db.execute(
                    "INSERT INTO loops(key, kind, text, urgency, since, first_seen) VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(key) DO UPDATE SET text=excluded.text, urgency=excluded.urgency",
                    (l.key, l.kind, l.text[:TEXT_CHARS], l.urgency if l.urgency in URGENCY else "today", l.since, now))
            self._db.commit()

    # ---- saying ----------------------------------------------------------------------------------
    def tick(self) -> list[str]:
        """Look at every source, and say what is due. Returns the sentences it said."""
        now = self._clock()
        self._observe(self._gather(), now)
        with self._lock:
            used = self._db.execute("SELECT COUNT(*) FROM nudge_log WHERE at>=?", (now - 24 * H,)).fetchone()[0]
            rows = self._db.execute("SELECT key, text, urgency, since, last_nudged, nudges FROM loops"
                                    " WHERE dismissed=0 AND snoozed_until<=? AND nudges<?", (now, MAX_NUDGES)).fetchall()
        due = []
        for key, text, urgency, since, last, nudges in rows:
            if now - since < GRACE_S[urgency]:
                continue
            if nudges and now - (last or 0) < COOLDOWN_S[urgency]:
                continue
            due.append((URGENCY[urgency], since, key, text))
        said: list[str] = []
        for _rank, _since, key, text in sorted(due):
            if used + len(said) >= self._max_per_day:
                break
            self._nudge(text)
            said.append(text)
            with self._lock:
                self._db.execute("UPDATE loops SET last_nudged=?, nudges=nudges+1 WHERE key=?", (now, key))
                self._db.execute("INSERT INTO nudge_log(at) VALUES (?)", (now,))
                self._db.execute("DELETE FROM nudge_log WHERE at<?", (now - 7 * 24 * H,))
                self._db.commit()
        return said

    # ---- the user's side ---------------------------------------------------------------------------
    def open_loops(self) -> list[Loop]:
        with self._lock:
            rows = self._db.execute("SELECT key, kind, text, since, urgency FROM loops WHERE dismissed=0").fetchall()
        return sorted((Loop(k, kd, t, s, u) for k, kd, t, s, u in rows), key=lambda l: (URGENCY[l.urgency], l.since))

    def review(self) -> str:
        """Everything open right now (it looks first, so the answer is not as old as the last tick)."""
        self._observe(self._gather(), self._clock())
        loops = self.open_loops()
        if not loops:
            return "Nothing needs you right now."
        now = self._clock()
        with self._lock:
            snoozed = {k for (k,) in self._db.execute("SELECT key FROM loops WHERE snoozed_until>?", (now,))}
        lines = [f"- [{l.key}] {l.text} ({age_words(max(0.0, now - l.since))})" + (" [snoozed]" if l.key in snoozed else "")
                 for l in loops]
        return f"Needs you ({len(loops)}):\n" + "\n".join(lines)

    def dismiss(self, key: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE loops SET dismissed=1 WHERE key=?", (key,))
            self._db.commit()
            return cur.rowcount == 1

    def snooze(self, key: str, hours: float) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE loops SET snoozed_until=? WHERE key=?",
                                   (self._clock() + max(0.0, float(hours)) * H, key))
            self._db.commit()
            return cur.rowcount == 1
