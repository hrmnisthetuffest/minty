"""What Minty learns about how you work, kept so you can look at it, correct it and remove it.

Five kinds, never mixed up: `explicit` (you said it: applies at once), `repeated` (a habit you accepted when it was offered),
`inferred` (a guess from one or two things you did: recorded, shown if you ask, changes nothing), `temporary` (for one task, it expires)
and `suggested` (a habit seen three times, offered to you and NOT applied until you accept). Each has a scope (global, research,
coding, mail, a project), a confidence that grows with evidence and halves when you correct it, a note on where it came from, and a
last-reinforced date. A guess nobody repeats fades; what you rejected or corrected away is not offered again for a month; what you told it
stays. Only a short list of kinds of preference exists (how research is kept, which senders matter or stay quiet), so a preference can
never reach a permission, a safety rule or a prompt: learning changes how Minty finishes a job, not what it is allowed to do.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

DAY = 86400.0
COOLDOWN_S = 30 * DAY
GUESS_FADES_S = 120 * DAY
SUGGEST_AT = 3
CONFIDENCE = {1: 0.3, 2: 0.5, 3: 0.75, 4: 0.85}
ADDRESS = r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,5}"
KEY = re.compile(rf"research->(?:sheet|doc)|mail\.(?:vip|mute):{ADDRESS}")
SCOPE = re.compile(r"[a-z][a-z0-9:_.-]{0,39}")
DESCRIPTIONS = (
    (re.compile(r"research->sheet"), lambda m: "Keep research in a sheet"),
    (re.compile(r"research->doc"), lambda m: "Write research up as a document"),
    (re.compile(r"mail\.vip:(.+)"), lambda m: f"Treat mail from {m.group(1)} as important"),
    (re.compile(r"mail\.mute:(.+)"), lambda m: f"Stay quiet about mail from {m.group(1)}"),
)
SOURCE_WORDS = {"explicit": "you said so", "repeated": "a habit you accepted", "inferred": "a guess from what you did",
                "temporary": "for one task", "suggested": "a habit I noticed, not applied until you accept"}


@dataclass(frozen=True)
class Pref:
    id: int
    key: str
    scope: str
    value: str
    source: str  # explicit | repeated | inferred | temporary
    status: str  # active | candidate | suggested | removed
    confidence: float
    evidence: int
    first_ts: float
    last_ts: float
    expires: float | None
    note: str


class PreferenceStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self._clock, self._lock = clock, threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS prefs (id INTEGER PRIMARY KEY, key TEXT NOT NULL, scope TEXT NOT NULL, value TEXT NOT NULL DEFAULT '1', "
            "source TEXT NOT NULL, status TEXT NOT NULL, confidence REAL NOT NULL, evidence INTEGER NOT NULL DEFAULT 0, first_ts REAL NOT NULL, "
            "last_ts REAL NOT NULL, expires REAL, blocked_until REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', UNIQUE (key, scope));"
            "CREATE TABLE IF NOT EXISTS refs (pref_id INTEGER NOT NULL, ref TEXT NOT NULL, PRIMARY KEY (pref_id, ref));")
        self._db.commit()

    @staticmethod
    def _check(key: str, scope: str) -> tuple[str, str]:
        key, scope = str(key or "").strip(), str(scope or "").strip()
        key = key.lower() if key.startswith("mail.") else key  # an address has no case
        if not KEY.fullmatch(key):
            raise ValueError("That is not a kind of preference Minty keeps.")
        if not SCOPE.fullmatch(scope):
            raise ValueError("A scope is a short plain word, like research, coding, mail or project:name.")
        return key, scope

    def _row(self, key: str, scope: str):
        return self._db.execute("SELECT * FROM prefs WHERE key = ? AND scope = ?", (key, scope)).fetchone()

    @staticmethod
    def _pref(r) -> Pref:
        return Pref(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[12])

    # ---- how a preference comes to exist -------------------------------------------------------------------------------
    def set_explicit(self, key: str, scope: str, value: str = "1", note: str = "") -> Pref:
        """The user said it: applies at once, whatever evidence there was or was not."""
        key, scope = self._check(key, scope)
        now = self._clock()
        with self._lock:
            self._db.execute(
                "INSERT INTO prefs (key, scope, value, source, status, confidence, evidence, first_ts, last_ts, note) VALUES (?,?,?, 'explicit', 'active', 1.0, 0, ?,?,?) "
                "ON CONFLICT(key, scope) DO UPDATE SET value = excluded.value, source = 'explicit', status = 'active', confidence = 1.0, last_ts = excluded.last_ts, "
                "expires = NULL, blocked_until = 0, note = excluded.note", (key, scope, str(value)[:40], now, now, str(note)[:200]))
            self._db.commit()
            return self._pref(self._row(key, scope))

    def set_temporary(self, key: str, scope: str, value: str, ttl_s: float) -> Pref:
        key, scope = self._check(key, scope)
        now = self._clock()
        with self._lock:
            self._db.execute(
                "INSERT INTO prefs (key, scope, value, source, status, confidence, evidence, first_ts, last_ts, expires) VALUES (?,?,?, 'temporary', 'active', 1.0, 0, ?,?,?) "
                "ON CONFLICT(key, scope) DO UPDATE SET value = excluded.value, source = 'temporary', status = 'active', last_ts = excluded.last_ts, expires = excluded.expires",
                (key, scope, str(value)[:40], now, now, now + float(ttl_s)))
            self._db.commit()
            return self._pref(self._row(key, scope))

    def observe(self, key: str, scope: str, ref: str = "", value: str = "1") -> Pref | None:
        """Something the user did that points at a habit. `ref` names the event (a goal, a thread) so the same one is never counted twice.
        One or two are a guess; three become a suggestion; nothing here ever applies without the user's yes."""
        key, scope = self._check(key, scope)
        now = self._clock()
        with self._lock:
            row = self._row(key, scope)
            if row is None:
                self._db.execute("INSERT INTO prefs (key, scope, value, source, status, confidence, evidence, first_ts, last_ts) VALUES (?,?,?, 'inferred', 'candidate', 0, 0, ?,?)",
                                 (key, scope, str(value)[:40], now, now))
                row = self._row(key, scope)
            pid, status, blocked = row[0], row[5], row[11]
            if status == "removed":
                if now < blocked:
                    return None  # it was said no to: not counted, not offered
                self._db.execute("UPDATE prefs SET status = 'candidate', source = 'inferred', evidence = 0, confidence = 0, blocked_until = 0 WHERE id = ?", (pid,))
                self._db.execute("DELETE FROM refs WHERE pref_id = ?", (pid,))
                status = "candidate"
            if status in ("active",):
                self._db.execute("UPDATE prefs SET last_ts = ? WHERE id = ?", (now, pid))  # reinforced
            elif ref and self._db.execute("SELECT 1 FROM refs WHERE pref_id = ? AND ref = ?", (pid, str(ref)[:80])).fetchone():
                pass  # the same event again
            else:
                if ref:
                    self._db.execute("INSERT OR IGNORE INTO refs (pref_id, ref) VALUES (?,?)", (pid, str(ref)[:80]))
                evidence = self._db.execute("SELECT evidence FROM prefs WHERE id = ?", (pid,)).fetchone()[0] + 1
                new_status = "suggested" if evidence >= SUGGEST_AT else status
                self._db.execute("UPDATE prefs SET evidence = ?, confidence = ?, last_ts = ?, status = ?, value = ? WHERE id = ?",
                                 (evidence, CONFIDENCE.get(evidence, 0.9), now, new_status, str(value)[:40], pid))
            self._db.commit()
            return self._pref(self._db.execute("SELECT * FROM prefs WHERE id = ?", (pid,)).fetchone())

    # ---- what the user does about it ------------------------------------------------------------------------------------
    def accept(self, pref_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE prefs SET status = 'active', source = 'repeated', last_ts = ? WHERE id = ? AND status = 'suggested'",
                                   (self._clock(), pref_id))
            self._db.commit()
        return cur.rowcount > 0

    def reject(self, pref_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE prefs SET status = 'removed', blocked_until = ?, note = 'you said no' WHERE id = ? AND status IN ('suggested','candidate','active')",
                                   (self._clock() + COOLDOWN_S, pref_id))
            self._db.commit()
        return cur.rowcount > 0

    def contradict(self, key: str, scope: str) -> str:
        """The user corrected what this led to: confidence halves, and a second correction removes it (and it is not offered again for a month)."""
        key, scope = self._check(key, scope)
        with self._lock:
            row = self._row(key, scope)
            if row is None or row[5] not in ("active", "suggested", "candidate"):
                return "unknown"
            confidence = row[6] / 2
            if confidence < 0.3:
                self._db.execute("UPDATE prefs SET status = 'removed', confidence = ?, blocked_until = ?, note = 'you corrected it' WHERE id = ?",
                                 (confidence, self._clock() + COOLDOWN_S, row[0]))
                result = "removed"
            else:
                self._db.execute("UPDATE prefs SET confidence = ? WHERE id = ?", (confidence, row[0]))
                result = "lowered"
            self._db.commit()
        return result

    def forget(self, pref_id: int) -> bool:
        with self._lock:
            self._db.execute("DELETE FROM refs WHERE pref_id = ?", (pref_id,))
            cur = self._db.execute("DELETE FROM prefs WHERE id = ?", (pref_id,))
            self._db.commit()
        return cur.rowcount > 0

    def decay(self) -> int:
        """A guess nobody repeated fades; what you told it stays."""
        with self._lock:
            old = self._clock() - GUESS_FADES_S
            ids = [r[0] for r in self._db.execute("SELECT id FROM prefs WHERE status IN ('candidate','suggested') AND last_ts < ?", (old,)).fetchall()]
            for pid in ids:
                self._db.execute("DELETE FROM refs WHERE pref_id = ?", (pid,))
                self._db.execute("DELETE FROM prefs WHERE id = ?", (pid,))
            self._db.commit()
        return len(ids)

    # ---- reading ------------------------------------------------------------------------------------------------------
    def value(self, key: str, scopes: tuple[str, ...]) -> str | None:
        """The value that applies here: the first scope, most specific first, with an active, unexpired preference."""
        now = self._clock()
        for scope in scopes:
            row = self._db.execute("SELECT value, expires FROM prefs WHERE key = ? AND scope = ? AND status = 'active'", (key, scope)).fetchone()
            if row and (row[1] is None or row[1] > now):
                return row[0]
        return None

    def list(self, status: str | None = None) -> list[Pref]:
        if status:
            rows = self._db.execute("SELECT * FROM prefs WHERE status = ? ORDER BY last_ts DESC, id DESC", (status,)).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM prefs WHERE status != 'removed' ORDER BY last_ts DESC, id DESC").fetchall()
        return [self._pref(r) for r in rows]

    def suggestions(self) -> list[Pref]:
        return self.list(status="suggested")

    def explain(self, p: Pref) -> str:
        what = next((fn(m) for rx, fn in DESCRIPTIONS if (m := rx.fullmatch(p.key))), p.key)
        when = datetime.fromtimestamp(p.last_ts).strftime("%b %d")
        extra = f" ({p.note})" if p.note else ""
        return (f"{what} — {SOURCE_WORDS.get(p.status if p.status == 'suggested' else p.source, p.source)}; "
                f"confidence {p.confidence:.0%}, last seen {when}, applies to {p.scope}{extra}")

    def close(self) -> None:
        with self._lock:
            self._db.close()
