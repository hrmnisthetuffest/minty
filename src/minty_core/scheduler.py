"""Reminders and Minty's own nudges, in one small SQLite table.

* A reminder the user set (`user_set`) fires at exactly its time, spoken and shown, whatever the policy says.
* Anything Minty starts by itself (a homework nudge, a digest) goes through `gate`: it is said, dropped, or held
  silently (shown on the display) and delivered as ONE digest when the gate lets it through.
* Items missed while the computer was off fire once when it comes back, marked late; nothing repeats.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from minty_core.tasks import spawn

log = logging.getLogger(__name__)

LATE_AFTER = timedelta(minutes=5)
MAX_DIGEST_CHARS = 420
HEARTBEAT_S = 30.0  # wake at least this often, so a clock change or a long sleep cannot strand an item


@dataclass(frozen=True)
class Item:
    id: int
    kind: str  # reminder | nudge | digest
    due: datetime
    text: str
    user_set: bool
    key: str | None
    status: str  # pending | held | fired | cancelled
    late: bool = False


class Scheduler:
    def __init__(self, path: Path, *, speak: Callable[[str], None], show: Callable[[str], None],
                 clock: Callable[[], datetime] = datetime.now,
                 gate: Callable[[Item, datetime], str] | None = None,
                 on_spoken: Callable[[datetime], None] | None = None):
        """gate(item, now) decides for Minty's own items: 'say' | 'hold' | 'drop' (no gate: say).
        on_spoken(now) is told when one was said aloud."""
        self._gate, self._on_spoken = gate, on_spoken
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._speak, self._show, self._clock = speak, show, clock
        self._actions: dict[str, Callable[[], object]] = {}
        self.housekeeping: Callable[[datetime], None] | None = None  # runs every wake-up, e.g. daily items
        self.halted: Callable[[], bool] | None = None  # True during a lockdown: nothing scheduled starts
        self._wake = asyncio.Event() if _loop_running() else None
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, due REAL NOT NULL,"
                " text TEXT NOT NULL, user_set INTEGER NOT NULL, key TEXT, status TEXT NOT NULL DEFAULT 'pending',"
                " created REAL NOT NULL, fired REAL, late INTEGER NOT NULL DEFAULT 0)")
            self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS items_key ON items(key) WHERE key IS NOT NULL"
                             " AND status IN ('pending','held')")
            self._db.commit()

    # -- writing ----------------------------------------------------------------------------------------
    def add(self, text: str, due: datetime, *, user_set: bool = True, kind: str = "reminder",
            key: str | None = None, once: bool = False) -> Item | None:
        """None when an item with the same key is already waiting (so a nudge is never queued twice). With
        once=True the key is also remembered after it fired: a daily item is created one time per day."""
        text = " ".join(str(text).split())[:300]
        if not text:
            raise ValueError("a reminder needs some words")
        if once and key is not None:
            with self._lock:
                if self._db.execute("SELECT 1 FROM items WHERE key=?", (key,)).fetchone():
                    return None
        try:
            with self._lock:
                cur = self._db.execute(
                    "INSERT INTO items(kind, due, text, user_set, key, created) VALUES (?,?,?,?,?,?)",
                    (kind, due.timestamp(), text, int(user_set), key, self._clock().timestamp()))
                self._db.commit()
                new_id = cur.lastrowid
        except sqlite3.IntegrityError:
            return None
        self.poke()
        return self.get(new_id)

    def cancel(self, ident: int) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE items SET status='cancelled' WHERE id=? AND status IN ('pending','held')", (ident,))
            self._db.commit()
            return cur.rowcount > 0

    def snooze(self, ident: int, minutes: int) -> Item | None:
        item = self.get(ident)
        if item is None or item.status == "cancelled":
            return None
        due = self._clock() + timedelta(minutes=max(1, min(minutes, 24 * 60)))
        with self._lock:
            self._db.execute("UPDATE items SET due=?, status='pending', late=0, fired=NULL WHERE id=?",
                             (due.timestamp(), ident))
            self._db.commit()
        self.poke()
        return self.get(ident)

    # -- reading ----------------------------------------------------------------------------------------
    def _row(self, r) -> Item:
        return Item(r[0], r[1], datetime.fromtimestamp(r[2]), r[3], bool(r[4]), r[5], r[6], bool(r[7]))

    _COLS = "id, kind, due, text, user_set, key, status, late"

    def get(self, ident: int) -> Item | None:
        with self._lock:
            r = self._db.execute(f"SELECT {self._COLS} FROM items WHERE id=?", (ident,)).fetchone()
        return self._row(r) if r else None

    def waiting(self) -> list[Item]:
        with self._lock:
            rows = self._db.execute(f"SELECT {self._COLS} FROM items WHERE status IN ('pending','held') "
                                    "ORDER BY due, id").fetchall()
        return [self._row(r) for r in rows]

    def next_due(self) -> datetime | None:
        pending = [i.due for i in self.waiting() if i.status == "pending"]
        return min(pending) if pending else None

    def register_action(self, name: str, fn: Callable[[], object]) -> None:
        """An item of kind 'action' whose text is `name` runs fn() (sync or a coroutine) instead of speaking."""
        self._actions[name] = fn

    def _run_action(self, name: str) -> None:
        fn = self._actions.get(name)
        if fn is None:
            log.warning("scheduled action %r is not registered", name)
            return
        try:
            out = fn()
            if asyncio.iscoroutine(out):
                spawn(out, name=f"scheduled action {name}")
        except Exception:  # noqa: BLE001
            log.exception("scheduled action %r failed", name)

    # -- delivery ---------------------------------------------------------------------------------------
    def tick(self, now: datetime | None = None) -> list[Item]:
        """Deliver what is due; returns what was spoken or digested this time."""
        now = now or self._clock()
        if self.halted is not None and self.halted():
            return []
        delivered: list[Item] = []
        if self.housekeeping is not None:
            try:
                self.housekeeping(now)
            except Exception:  # noqa: BLE001
                log.exception("scheduler housekeeping failed")
        for item in self.waiting():
            if item.status == "pending" and item.due <= now:
                if item.kind == "action":
                    self._finish(item, now)
                    self._run_action(item.text)
                elif item.user_set:
                    self._finish(item, now)
                    self._speak(self._line(item, now))
                    self._show(item.text)
                    delivered.append(item)
                else:
                    verdict = self._verdict(item, now)
                    if verdict == "drop":
                        self.cancel(item.id)
                    elif verdict == "hold":
                        self._hold(item)
                        self._show(item.text)  # silently, on the display
                    else:
                        self._finish(item, now)
                        self._speak(self._line(item, now))
                        self._show(item.text)
                        self._spoken(now)
                        delivered.append(item)
        held = [i for i in self.waiting() if i.status == "held" and i.due <= now and not i.user_set
                and self._verdict(i, now) == "say"]
        if held:
            for item in held:
                self._finish(item, now)
            self._speak(self._digest(held))
            self._spoken(now)
            delivered.extend(held)
        return delivered

    def _verdict(self, item: Item, now: datetime) -> str:
        return self._gate(item, now) if self._gate is not None else "say"

    def _spoken(self, now: datetime) -> None:
        if self._on_spoken is not None:
            self._on_spoken(now)

    def _line(self, item: Item, now: datetime) -> str:
        late = now - item.due > LATE_AFTER
        return f"Reminder: {item.text}." + (" This was due earlier." if late else "") if item.kind == "reminder" \
            else item.text

    @staticmethod
    def _digest(items: list[Item]) -> str:
        head = "Earlier: " if len(items) > 1 else ""
        parts, used = [], len(head)
        for i, item in enumerate(items):
            piece = item.text.rstrip(".")
            if used + len(piece) > MAX_DIGEST_CHARS and parts:
                parts.append(f"and {len(items) - i} more")
                break
            parts.append(piece)
            used += len(piece) + 2
        return head + "; ".join(parts) + "."

    def _finish(self, item: Item, now: datetime) -> None:
        with self._lock:
            self._db.execute("UPDATE items SET status='fired', fired=?, late=? WHERE id=?",
                             (now.timestamp(), int(now - item.due > LATE_AFTER), item.id))
            self._db.commit()

    def _hold(self, item: Item) -> None:
        with self._lock:
            self._db.execute("UPDATE items SET status='held' WHERE id=?", (item.id,))
            self._db.commit()

    # -- the background task ----------------------------------------------------------------------------
    def poke(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def run(self) -> None:
        self._wake = asyncio.Event()
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — a bad row must not stop every later reminder
                log.exception("scheduler tick failed")
            nxt = self.next_due()
            wait = HEARTBEAT_S if nxt is None else max(0.2, min(HEARTBEAT_S, (nxt - self._clock()).total_seconds()))
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
