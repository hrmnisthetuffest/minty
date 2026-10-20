"""The notification reader (639), opt-in per app. It listens to desktop notification messages (dbus-monitor), keeps a short
list IN MEMORY for the apps you chose, drops everything else the moment it arrives, never writes to disk and speaks only
when you ask. It is a reader, not a responder: it cannot reply to or act on a notification."""
from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

TITLE_MAX, BODY_MAX = 60, 160
WATCH = "interface='org.freedesktop.Notifications',member='Notify'"


@dataclass
class Note:
    app: str
    title: str
    body: str
    time: float = 0.0


def parse_notify(block: str) -> Note | None:
    head = str(block).split("\n", 1)[0]
    if "member=Notify" not in head or "org.freedesktop.Notifications" not in head:
        return None
    strings = re.findall(r'^   string "(.*?)"$', str(block), re.M | re.S)
    if len(strings) < 4:
        return None
    return Note(strings[0], strings[2], strings[3])


class NotificationReader:
    def __init__(self, apps: Callable[[], list[str]], clock: Callable[[], float] = time.time, keep: int = 20,
                 ttl_s: float = 3600.0):
        self._apps, self._clock, self.ttl = apps, clock, ttl_s
        self._notes: deque[Note] = deque(maxlen=keep)

    def _allowed(self, app: str) -> bool:
        low = app.lower()
        return any(a.strip() and a.strip().lower() in low for a in self._apps())

    def feed(self, note: Note | None) -> None:
        if note is None or not self._allowed(note.app):
            return  # not an app you chose: gone before it is ever held
        self._notes.append(Note(note.app, " ".join(note.title.split())[:TITLE_MAX],
                                " ".join(note.body.split())[:BODY_MAX], self._clock()))

    def pending(self) -> list[Note]:
        cutoff = self._clock() - self.ttl
        while self._notes and self._notes[0].time < cutoff:
            self._notes.popleft()
        return list(self._notes)

    def read(self, app: str | None = None) -> str:
        notes = self.pending()
        chosen = [n for n in notes if not app or app.lower() in n.app.lower()]
        if not chosen:
            return "No new notifications from the apps you chose."
        for n in chosen:
            self._notes.remove(n)
        lines = "; ".join(f"{n.title}: {n.body}" if n.body else n.title for n in chosen)
        return f"{len(chosen)} notification{'s' * (len(chosen) != 1)}: {lines}."


async def monitor(reader: NotificationReader, spawn=asyncio.create_subprocess_exec, retry_s: float = 30.0,
                  once: bool = False) -> None:
    """Feed the reader from `dbus-monitor` until cancelled; if it ends, start it again after a pause."""
    while True:
        try:
            proc = await spawn("dbus-monitor", WATCH, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                               stderr=asyncio.subprocess.DEVNULL)
        except (FileNotFoundError, OSError):
            return  # no dbus-monitor on this computer: nothing to read
        block: list[str] = []
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line and not line[0].isspace() and block:
                    reader.feed(parse_notify("\n".join(block)))
                    block = []
                block.append(line)
            reader.feed(parse_notify("\n".join(block)))
        finally:
            proc.kill()
            await proc.wait()
        if once:
            return
        await asyncio.sleep(retry_s)
