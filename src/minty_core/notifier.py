"""One place that decides who is told what, when and how.

Sources (the mail watcher, a failed job, a deadline) say *what happened* and how serious it is: `info` is recorded and nothing more,
`notable` goes to the screen and the next digest, `important` is said aloud and sent to the phone, `urgent` is the same and may break
the quiet hours if the policy says so. This layer owns the rest, so no source has to: it never tells the same news twice (a new
fact, or a higher severity, is news again), keeps a burst from becoming a flood, holds what would make a sound while it is quiet,
keeps private detail off channels that leave the computer, retries a channel that failed a few times and then says it gave up, and
says an urgent thing nobody dealt with again a couple of times. Plain rules, no model. There is no phone-call channel: none exists here.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

SEVERITIES = ("info", "notable", "important", "urgent")
RANK = {name: i for i, name in enumerate(SEVERITIES)}
RETRY_AFTER_S = (60, 300, 1800)  # the wait after the first, second and third failure; the third failure is the last
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_HIDDEN = {"Cc", "Cf", "Co", "Cs"}
LOUD_CHARS = 240
QUIET_CHARS = 420


@dataclass(frozen=True)
class Event:
    source: str  # who is telling: "gmail:work"
    key: str  # what it is about, stable: a thread, a job id
    severity: str
    title: str  # one plain line, fine on any screen
    body: str = ""  # the summary: private channels only if `detail_private`
    action: str = ""  # the recommended next step
    detail_private: bool = False
    fingerprint: str = ""  # changes when the situation does; the same key with the same fingerprint is the same news
    taken: str = ""  # what Minty already did about it


@dataclass(frozen=True)
class Channel:
    name: str
    send: Callable[[Event, str], None]
    loud: bool = False  # makes a sound or buzzes a phone: held in quiet hours
    private: bool = True  # may carry private detail (false: it leaves the computer)


@dataclass
class Policy:
    quiet_from: int = 22  # hours
    quiet_to: int = 7
    urgent_bypass_quiet: bool = False  # the explicit policy: only urgent may break the quiet, and only if this is on
    max_important_per_hour: int = 4
    max_urgent_per_hour: int = 10
    remind_after_h: float = 4.0
    max_reminders: int = 2
    routes: dict = field(default_factory=lambda: {
        "info": ("record",), "notable": ("record", "digest", "panel"),
        "important": ("record", "panel", "say", "phone"), "urgent": ("record", "panel", "say", "phone")})


@dataclass
class Outcome:
    delivered: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    held: bool = False
    suppressed: str = ""


def clean(text: str, limit: int) -> str:
    """One plain line: no links, no hidden or control characters."""
    text = _URL.sub("[link]", str(text or ""))
    text = "".join(" " if c in "\r\n\t" else c for c in text if unicodedata.category(c) not in _HIDDEN or c in "\r\n\t")
    return " ".join(text.split())[:limit]


class Notifier:
    def __init__(self, path: Path, channels: list[Channel], policy: Policy | Callable[[], Policy] | None = None, *,
                 clock: Callable[[], float] = time.time, hour_of: Callable[[float], int] = lambda t: datetime.fromtimestamp(t).hour):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self._channels = {c.name: c for c in channels}
        self._policy = policy if callable(policy) else (lambda p=policy or Policy(): p)
        self._clock, self._hour_of = clock, hour_of
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, source TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL DEFAULT '', "
            "severity TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '', "
            "detail_private INTEGER NOT NULL DEFAULT 0, first_ts REAL NOT NULL, last_ts REAL NOT NULL, told_ts REAL, told_rank INTEGER NOT NULL DEFAULT -1, "
            "reminders INTEGER NOT NULL DEFAULT 0, resolved INTEGER NOT NULL DEFAULT 0, UNIQUE (source, key));"
            "CREATE TABLE IF NOT EXISTS deliveries (id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, channel TEXT NOT NULL, text TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, next_try REAL, status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '');"
            "CREATE TABLE IF NOT EXISTS tells (ts REAL NOT NULL, severity TEXT NOT NULL);")
        self._db.commit()

    # ---- the one entry point -------------------------------------------------------------------------------------
    def publish(self, event: Event) -> Outcome:
        severity = event.severity if event.severity in RANK else "notable"
        event = Event(event.source, clean(event.key, 120), severity, clean(event.title, 160), clean(event.body, 300), clean(event.action, 120),
                      event.detail_private, clean(event.fingerprint, 80), clean(event.taken, 120))
        now = self._clock()
        with self._lock:
            row = self._db.execute("SELECT id, fingerprint, told_rank FROM events WHERE source = ? AND key = ?", (event.source, event.key)).fetchone()
            rank, update = RANK[severity], False
            if row is None:
                event_id = self._db.execute(
                    "INSERT INTO events (source, key, fingerprint, severity, title, body, action, detail_private, first_ts, last_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (event.source, event.key, event.fingerprint, severity, event.title, event.body, event.action, int(event.detail_private), now, now)).lastrowid
            else:
                event_id, old_fingerprint, told_rank = row
                changed = old_fingerprint != event.fingerprint
                self._db.execute("UPDATE events SET fingerprint = ?, severity = ?, title = ?, body = ?, action = ?, detail_private = ?, last_ts = ? WHERE id = ?",
                                 (event.fingerprint, severity, event.title, event.body, event.action, int(event.detail_private), now, event_id))
                if rank > told_rank:
                    pass  # more serious than anything said so far: news, said plainly
                elif not changed:
                    self._db.commit()
                    return Outcome(suppressed="already told")
                elif rank < told_rank:
                    self._db.commit()
                    return Outcome(suppressed="less serious than what was said")
                else:
                    update = True
            self._db.commit()
            return self._tell(event_id, event, "Update: " if update else "", now)

    def _tell(self, event_id: int, event: Event, prefix: str, now: float) -> Outcome:
        policy = self._policy()
        out = Outcome()
        names = list(policy.routes.get(event.severity, ("record",)))
        loud = [n for n in names if n in self._channels and self._channels[n].loud]
        if loud:
            quiet = self._quiet(policy, now)
            capped = self._over_cap(policy, event.severity, now)
            if (quiet and not (event.severity == "urgent" and policy.urgent_bypass_quiet)) or capped:
                names = [n for n in names if n not in loud]
                names += [n for n in ("digest", "panel") if n not in names]  # it waits where nothing makes a sound, and is not lost
                out.held = True
        for name in names:
            channel = self._channels.get(name)
            if channel is None:
                continue
            text = prefix + self._text(channel, event)
            ok = self._send(event_id, channel, event, text)
            (out.delivered if ok else out.failed).append(name)
        if loud and not out.held:
            self._db.execute("INSERT INTO tells (ts, severity) VALUES (?, ?)", (now, event.severity))
        self._db.execute("UPDATE events SET told_ts = ?, told_rank = ?, resolved = 0, reminders = 0 WHERE id = ?", (now, RANK[event.severity], event_id))
        self._db.commit()
        return out

    # ---- delivery ----------------------------------------------------------------------------------------------------
    @staticmethod
    def _text(channel: Channel, event: Event) -> str:
        limit = LOUD_CHARS if channel.loud else QUIET_CHARS
        if event.detail_private and not channel.private:
            return clean(f"{event.title}. Details are on the laptop.", limit)
        parts = [event.title, event.body, f"Next: {event.action}" if event.action else "", f"Done: {event.taken}" if event.taken else ""]
        return clean(". ".join(p.rstrip(".") for p in parts if p), limit)

    def _send(self, event_id: int, channel: Channel, event: Event, text: str) -> bool:
        try:
            channel.send(event, text)
        except Exception as e:  # noqa: BLE001 — one channel being down must not stop the others
            self._db.execute("INSERT INTO deliveries (event_id, channel, text, attempts, next_try, status, error) VALUES (?,?,?,1,?, 'pending', ?)",
                             (event_id, channel.name, text, self._clock() + RETRY_AFTER_S[0], clean(f"{type(e).__name__}: {e}", 120)))
            return False
        self._db.execute("INSERT INTO deliveries (event_id, channel, text, attempts, status) VALUES (?,?,?,1, 'ok')", (event_id, channel.name, text))
        return True

    def retry_due(self) -> int:
        """Try the deliveries that failed once their wait is over; three tries in all. Returns how many got through."""
        now, delivered = self._clock(), 0
        with self._lock:
            rows = self._db.execute(
                "SELECT d.id, d.channel, d.text, d.attempts, e.source, e.key, e.severity, e.title, e.body, e.action, e.detail_private, e.fingerprint "
                "FROM deliveries d JOIN events e ON e.id = d.event_id WHERE d.status = 'pending' AND d.next_try <= ?", (now,)).fetchall()
            for did, name, text, attempts, source, key, severity, title, body, action, private, fingerprint in rows:
                channel = self._channels.get(name)
                if channel is None:
                    self._db.execute("UPDATE deliveries SET status = 'failed', error = 'no such channel' WHERE id = ?", (did,))
                    continue
                try:
                    channel.send(Event(source, key, severity, title, body, action, bool(private), fingerprint), text)
                except Exception as e:  # noqa: BLE001
                    attempts += 1
                    if attempts >= len(RETRY_AFTER_S):
                        self._db.execute("UPDATE deliveries SET status = 'failed', attempts = ?, error = ? WHERE id = ?",
                                         (attempts, clean(f"{type(e).__name__}: {e}", 120), did))
                    else:
                        self._db.execute("UPDATE deliveries SET attempts = ?, next_try = ?, error = ? WHERE id = ?",
                                         (attempts, now + RETRY_AFTER_S[attempts - 1], clean(f"{type(e).__name__}: {e}", 120), did))
                    continue
                self._db.execute("UPDATE deliveries SET status = 'ok', attempts = attempts + 1, error = '' WHERE id = ?", (did,))
                delivered += 1
            self._db.commit()
        return delivered

    def attempts(self, source: str, key: str, channel: str) -> int:
        row = self._db.execute("SELECT MAX(d.attempts) FROM deliveries d JOIN events e ON e.id = d.event_id WHERE e.source = ? AND e.key = ? AND d.channel = ?",
                               (source, key, channel)).fetchone()
        return int(row[0] or 0)

    def failed(self) -> list[tuple[str, str, str, str]]:
        """(source, key, channel, error) for deliveries that were given up on."""
        return self._db.execute("SELECT e.source, e.key, d.channel, d.error FROM deliveries d JOIN events e ON e.id = d.event_id WHERE d.status = 'failed' "
                                "ORDER BY d.id").fetchall()

    # ---- reminders, resolving, looking ------------------------------------------------------------------------------
    def remind_due(self) -> int:
        """Say an urgent thing nobody dealt with again, a couple of times, never in the quiet hours and never once it is resolved."""
        policy, now, told = self._policy(), self._clock(), 0
        with self._lock:
            rows = self._db.execute(
                "SELECT id, source, key, severity, title, body, action, detail_private, fingerprint FROM events WHERE resolved = 0 AND told_rank = ? "
                "AND reminders < ? AND told_ts <= ?", (RANK["urgent"], policy.max_reminders, now - policy.remind_after_h * 3600)).fetchall()
            for event_id, source, key, severity, title, body, action, private, fingerprint in rows:
                if self._quiet(policy, now) and not policy.urgent_bypass_quiet:
                    continue
                self._db.execute("UPDATE events SET reminders = reminders + 1, told_ts = ? WHERE id = ?", (now, event_id))
                event = Event(source, key, severity, title, body, action, bool(private), fingerprint)
                for name in policy.routes.get("urgent", ()):
                    if name in ("record", "digest"):
                        continue
                    channel = self._channels.get(name)
                    if channel is not None:
                        self._send(event_id, channel, event, "Reminder: " + self._text(channel, event))
                told += 1
            self._db.commit()
        return told

    def resolve(self, source: str, key: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE events SET resolved = 1 WHERE source = ? AND key = ?", (source, key))
            self._db.commit()
        return cur.rowcount > 0

    def resolve_by_key(self, key: str) -> bool:
        """You dealt with it (by whatever source told you): stop reminding."""
        with self._lock:
            cur = self._db.execute("UPDATE events SET resolved = 1 WHERE key = ?", (str(key),))
            self._db.commit()
        return cur.rowcount > 0

    def recent(self, limit: int = 20) -> list[dict]:
        rows = self._db.execute("SELECT source, key, severity, title, last_ts, resolved FROM events ORDER BY last_ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("source", "key", "severity", "title", "last_ts", "resolved"), r)) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- the rules ----------------------------------------------------------------------------------------------------
    def _quiet(self, policy: Policy, now: float) -> bool:
        hour, start, end = self._hour_of(now), policy.quiet_from, policy.quiet_to
        return start <= hour or hour < end if start > end else start <= hour < end

    def _over_cap(self, policy: Policy, severity: str, now: float) -> bool:
        cap = {"important": policy.max_important_per_hour, "urgent": policy.max_urgent_per_hour}.get(severity)
        if cap is None:
            return False
        count = self._db.execute("SELECT COUNT(*) FROM tells WHERE severity = ? AND ts > ?", (severity, now - 3600)).fetchone()[0]
        return count >= cap
