"""The Gmail watcher: notices what matters in your mailbox without being asked, and costs as little as it can.

How it stays small: it asks Gmail for what changed since a history position it remembers (an incremental list of message ids and
labels, not the mailbox), skips what Gmail itself filed as promotions, social or forums, spam, drafts or sent mail without downloading it,
downloads only the headers and snippet of the rest, a bounded number per round, and remembers every message it has handled so nothing is
looked at or told twice. The first look only notes where the mailbox is: old mail is never announced. A source that fails is retried
with a growing wait, and after three failures it says so once instead of every ten minutes. It only reads: the source it is given has no
way to send, label or delete, and what a message says (sender, subject, snippet) is shown as plain text, never followed.
Whether a message matters is `mailscore`; who hears about it, how and when is `notifier`.
"""
from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from minty_core.mailpolicy import DEFAULT_SENSITIVE
from minty_core.mailscore import LOW_CATEGORIES, TIME, Context, Mail, address_of, deadline_in, name_of, score
from minty_core.notifier import RANK, Event, Notifier

log = logging.getLogger(__name__)
SKIP_LABELS = frozenset({"SENT", "DRAFT", "SPAM", "TRASH"})
KNOWN_FOR_S = 30 * 86400
ACTIONS = {"reply": "reply to them", "decide": "decide and answer", "pay": "pay it or reply", "check": "check it is real before doing anything",
           "read": "read it", "none": ""}


class HistoryExpired(Exception):
    """Gmail no longer has the history position (it keeps about a week): start again from recent mail."""


class MailSource(Protocol):
    async def accounts(self) -> list[str]: ...
    async def baseline(self, account: str) -> str: ...
    async def new_messages(self, account: str, history_id: str) -> tuple[list[dict], str]: ...
    async def recent_messages(self, account: str, minutes: int) -> list[dict]: ...
    async def metadata(self, account: str, message_id: str) -> Mail: ...
    async def sender_known(self, account: str, address: str) -> bool: ...


@dataclass(frozen=True)
class WatchSettings:
    enabled: bool = False  # off until the user turns it on: it reads their mail
    accounts: tuple[str, ...] = ()  # empty: every connected account
    me: dict | None = None  # account -> its own address, for "addressed to you"
    interval_s: float = 600.0
    vips: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    max_per_poll: int = 20
    min_confidence: float = 0.5  # below this an "important" result is only "notable"
    lookup_senders: bool = True
    max_lookups: int = 5
    reminders: bool = True  # an important mail with a deadline a few days off gets a reminder the morning before


class WatchStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        import os
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self._clock, self._lock = clock, threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS accounts (alias TEXT PRIMARY KEY, history_id TEXT NOT NULL DEFAULT '', last_ok REAL NOT NULL DEFAULT 0, "
            "failures INTEGER NOT NULL DEFAULT 0, next_poll REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '');"
            "CREATE TABLE IF NOT EXISTS seen (account TEXT NOT NULL, message_id TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (account, message_id));"
            "CREATE TABLE IF NOT EXISTS senders (account TEXT NOT NULL, address TEXT NOT NULL, known INTEGER NOT NULL, ts REAL NOT NULL, "
            "PRIMARY KEY (account, address));"
            "CREATE TABLE IF NOT EXISTS threads (thread_id TEXT PRIMARY KEY, sender TEXT NOT NULL, ts REAL NOT NULL);")
        self._db.commit()

    def account(self, alias: str) -> dict:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO accounts (alias) VALUES (?)", (alias,))
            self._db.commit()
            row = self._db.execute("SELECT history_id, last_ok, failures, next_poll, last_error FROM accounts WHERE alias = ?", (alias,)).fetchone()
        return dict(zip(("history_id", "last_ok", "failures", "next_poll", "last_error"), row))

    def set_history(self, alias: str, history_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE accounts SET history_id = ? WHERE alias = ?", (str(history_id), alias))
            self._db.commit()

    def ok(self, alias: str) -> None:
        with self._lock:
            self._db.execute("UPDATE accounts SET last_ok = ?, failures = 0, next_poll = 0, last_error = '' WHERE alias = ?", (self._clock(), alias))
            self._db.commit()

    def failed(self, alias: str, wait_s: float, error: str) -> int:
        with self._lock:
            self._db.execute("UPDATE accounts SET failures = failures + 1, next_poll = ?, last_error = ? WHERE alias = ?",
                             (self._clock() + wait_s, error[:120], alias))
            self._db.commit()
            return self._db.execute("SELECT failures FROM accounts WHERE alias = ?", (alias,)).fetchone()[0]

    def note_thread(self, thread_id: str, sender: str) -> None:
        """Who a thread's notice was about, so "that one mattered" or "that was noise" can be taken as a hint about the sender."""
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO threads (thread_id, sender, ts) VALUES (?,?,?)", (thread_id, sender, self._clock()))
            self._db.execute("DELETE FROM threads WHERE ts < ?", (self._clock() - 60 * 86400,))
            self._db.commit()

    def sender_of(self, thread_id: str) -> str:
        row = self._db.execute("SELECT sender FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
        return row[0] if row else ""

    def aliases(self) -> list[str]:
        return [r[0] for r in self._db.execute("SELECT alias FROM accounts ORDER BY alias").fetchall()]

    def seen(self, alias: str, message_id: str) -> bool:
        return self._db.execute("SELECT 1 FROM seen WHERE account = ? AND message_id = ?", (alias, message_id)).fetchone() is not None

    def mark_seen(self, alias: str, message_id: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO seen (account, message_id, ts) VALUES (?,?,?)", (alias, message_id, self._clock()))
            self._db.execute("DELETE FROM seen WHERE ts < ?", (self._clock() - 90 * 86400,))
            self._db.commit()

    def sender(self, alias: str, address: str) -> bool | None:
        row = self._db.execute("SELECT known, ts FROM senders WHERE account = ? AND address = ?", (alias, address)).fetchone()
        return bool(row[0]) if row and self._clock() - row[1] < KNOWN_FOR_S else None

    def set_sender(self, alias: str, address: str, known: bool) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO senders (account, address, known, ts) VALUES (?,?,?,?)", (alias, address, int(known), self._clock()))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()


class GmailWatcher:
    def __init__(self, source: MailSource, store: WatchStore, notifier: Notifier, settings: Callable[[], WatchSettings], *,
                 clock: Callable[[], float] = time.time, halted: Callable[[], bool] = lambda: False,
                 prefs: Callable[[str], str | None] = lambda key: None,
                 remind: Callable[[str, datetime, str], None] | None = None):
        self._source, self._store, self._notifier, self._settings, self._clock, self._halted = source, store, notifier, settings, clock, halted
        self._remind = remind  # (text, when, key): the scheduler's own gate decides whether and how it is said
        self._prefs = prefs  # what you accepted or said about senders (mail.vip:, mail.mute:); never anything about permissions
        self._round_wait, self._round_failures = 0.0, 0

    async def poll_once(self) -> dict:
        """One round over every account, inside one session if the source wants one. Never raises: a failure is counted, waited out and,
        after three, said once; a tool you have not allowed is said once, with the command that allows it."""
        settings = self._settings()
        if not settings.enabled or self._halted():
            return {}
        if self._round_wait > self._clock():
            return {}
        opener = getattr(self._source, "round", None)
        try:
            async with (opener() if opener is not None else contextlib.nullcontext()):
                report = await self._accounts_round(settings)
            self._round_failures = 0
            return report
        except PermissionError as e:
            self._round_wait = self._clock() + settings.interval_s * 6
            self._notifier.publish(Event("gmailwatch", "needs-allow", "notable", "Gmail watching is waiting for you to allow Composio's tool",
                                         str(e)[:200], "run the command above, then it starts by itself", fingerprint="allow"))
        except Exception as e:  # noqa: BLE001 — Composio unreachable, a bad key: waited out, not retried every second
            self._round_failures += 1
            self._round_wait = self._clock() + min(3600.0, settings.interval_s * 2 ** self._round_failures)
            log.warning("gmail watcher round failed (%d): %s", self._round_failures, e)
            if self._round_failures == 3:
                self._notifier.publish(Event("gmailwatch", "down:round", "notable", "I could not reach your Gmail through Composio",
                                             "It has failed three times in a row; I keep trying with longer waits.", "check the Composio connection",
                                             fingerprint="down"))
        return {}

    async def _accounts_round(self, settings: WatchSettings) -> dict:
        accounts = list(settings.accounts) or await self._source.accounts()
        report = {}
        for alias in accounts:
            state = self._store.account(alias)
            if state["next_poll"] > self._clock():
                continue
            try:
                report[alias] = await self._poll(alias, settings)
                if state["failures"] >= 3:
                    self._notifier.resolve("gmailwatch", f"down:{alias}")
                self._store.ok(alias)
            except Exception as e:  # noqa: BLE001 — a failing mailbox check is waited out, not retried every second
                failures = self._store.failed(alias, min(3600.0, settings.interval_s * 2 ** (state["failures"] + 1)), f"{type(e).__name__}: {e}")
                log.warning("gmail watcher: %s failed (%d): %s", alias, failures, e)
                if failures == 3:
                    self._notifier.publish(Event("gmailwatch", f"down:{alias}", "notable", f"I could not check your Gmail ({alias})",
                                                 "It has failed three times in a row; I keep trying with longer waits.", "check the Composio connection",
                                                 fingerprint="down"))
        return report

    async def _poll(self, alias: str, settings: WatchSettings) -> int:
        state = self._store.account(alias)
        if not state["history_id"]:  # the first look: where the mailbox is now, nothing about what is already there
            self._store.set_history(alias, await self._source.baseline(alias))
            return 0
        try:
            items, new_history = await self._source.new_messages(alias, state["history_id"])
        except HistoryExpired:
            items = await self._source.recent_messages(alias, 180)
            new_history = await self._source.baseline(alias)
        picked, ids = [], set()
        for item in items:
            mid, labels = str(item.get("id") or ""), set(item.get("labels") or [])
            if not mid or mid in ids or self._store.seen(alias, mid):
                continue
            ids.add(mid)
            if "INBOX" not in labels or labels & SKIP_LABELS or labels & set(LOW_CATEGORIES) - {"CATEGORY_UPDATES"}:
                self._store.mark_seen(alias, mid)  # never downloaded: Gmail already said what it is
                continue
            picked.append(mid)
        batch = picked[:settings.max_per_poll]
        lookups = [0]
        for mid in batch:
            mail = await self._source.metadata(alias, mid)
            self._tell(alias, mail, await self._context(alias, mail, settings, lookups), settings)
            self._store.mark_seen(alias, mid)
        if len(picked) <= settings.max_per_poll:  # otherwise the rest are looked at next time, from the same position
            self._store.set_history(alias, new_history)
        return len(batch)

    async def _context(self, alias: str, mail: Mail, settings: WatchSettings, lookups: list[int]) -> Context:
        address = address_of(mail.sender)
        known = self._store.sender(alias, address) if address else None
        if known is None and address and settings.lookup_senders and lookups[0] < settings.max_lookups:
            lookups[0] += 1
            try:
                known = bool(await self._source.sender_known(alias, address))
                self._store.set_sender(alias, address, known)
            except Exception:  # noqa: BLE001 — not knowing is allowed
                known = None
        me = (settings.me or {}).get(alias)
        vips = settings.vips + ((address,) if address and self._prefs(f"mail.vip:{address}") == "1" else ())
        return Context(me=(me,) if me else (), vips=vips, topics=settings.topics, known=lambda a, k=known: k if a == address else None)

    def _tell(self, alias: str, mail: Mail, ctx: Context, settings: WatchSettings) -> None:
        verdict = score(mail, ctx)
        severity = verdict.severity
        address = address_of(mail.sender)
        if address:
            self._store.note_thread(mail.thread_id or mail.id, address)
            if severity != "urgent" and self._prefs(f"mail.mute:{address}") == "1":
                severity = "info"  # you asked for quiet about this sender; only an urgent one still gets through
        if RANK[severity] >= RANK["important"] and verdict.confidence < settings.min_confidence:
            severity = "notable"  # not sure enough to interrupt
        who = name_of(mail.sender)
        sensitive = any(re.search(rf"\b{re.escape(w)}\b", mail.subject, re.IGNORECASE) for w in DEFAULT_SENSITIVE)  # whole words: "shipping" is not a PIN
        title = f"Mail from {who}" if sensitive else f"Mail from {who}: {mail.subject or '(no subject)'}"
        why = "; ".join(verdict.reasons[:2])
        body = " ".join(x for x in ((mail.subject + ".") if sensitive else "", mail.snippet[:140], f"Why: {why}." if why and severity != "info" else "") if x)
        self._notifier.publish(Event(f"gmail:{alias}", mail.thread_id or mail.id, severity, title, body, ACTIONS.get(verdict.action, ""),
                                     detail_private=True, fingerprint=mail.id))
        self._deadline(mail, severity, title, settings)

    def _deadline(self, mail: Mail, severity: str, title: str, settings: WatchSettings) -> None:
        """A mail that matters and says when something is due, a few days away, gets one reminder the morning before: the notice already
        covers the near ones, and what is not worth a notice is not worth a reminder."""
        if self._remind is None or not settings.reminders or RANK[severity] < RANK["important"]:
            return
        text = f"{mail.subject} {mail.snippet}"
        if not TIME.search(text):
            return
        today = datetime.fromtimestamp(self._clock()).date()
        due = deadline_in(text, today)
        if due is None or (due - today).days < 2:
            return
        when = datetime.combine(due - timedelta(days=1), dtime(9, 0))
        try:
            self._remind(f"Deadline tomorrow: {title}", when, f"mail-deadline:{mail.thread_id or mail.id}:{due.isoformat()}")
        except Exception:  # noqa: BLE001 — a reminder that cannot be made must not lose the notice
            log.warning("could not make a deadline reminder", exc_info=True)
