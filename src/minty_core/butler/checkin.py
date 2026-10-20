"""The check-in timer (951): opt-in and plain. You say "check in with me in two hours"; at the time it asks if you
are okay; if you do not answer within a few minutes it sends one fixed-text message to the contact you chose.
That contact is a person who started the bot, kept apart from the people who can command Minty. The message holds
no location and no data about you."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text
from minty_core.timefmt import clock12

MIN_MINUTES, MAX_MINUTES = 5, 24 * 60


class CheckInError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    kind: str  # ask | contact
    text: str
    label: str = ""


def template(user: str, due: datetime, label: str = "") -> str:
    who = (user or "someone you know").strip()
    what = f" ({label.strip()[:40]})" if label.strip() else ""
    return (f"This is Minty, {who}'s assistant. {who} asked me to check in at {clock12(due)}{what} and I have not "
            f"heard back. Please try to reach them. I can't tell you anything else.")


class CheckIn:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock

    def _load(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("due") else None
        except (OSError, ValueError):
            return None

    def _save(self, data: dict | None) -> None:
        atomic_write_text(self.path, json.dumps(data or {}), mode=0o600)

    def state(self) -> dict | None:
        return self._load()

    def start(self, minutes: int, label: str = "") -> dict:
        if not MIN_MINUTES <= int(minutes) <= MAX_MINUTES:
            raise CheckInError(f"A check-in is between {MIN_MINUTES} minutes and 24 hours.")
        now = self._clock()
        data = {"due": now + int(minutes) * 60, "label": " ".join(str(label).split())[:60], "started": now,
                "asked_at": None}
        self._save(data)
        return data

    def confirm(self) -> bool:
        if self._load() is None:
            return False
        self._save(None)
        return True

    cancel = confirm

    def tick(self, user: str = "", grace_min: int = 10) -> list[Action]:
        data = self._load()
        if data is None:
            return []
        now = self._clock()
        if data.get("asked_at") is None:
            if now >= data["due"]:
                data["asked_at"] = now
                self._save(data)
                return [Action("ask", "Check-in: are you okay? Say \"I'm fine\" to close it.", data.get("label", ""))]
            return []
        if now >= data["asked_at"] + grace_min * 60:
            self._save(None)  # told once, then closed
            return [Action("contact", template(user, datetime.fromtimestamp(data["due"]), data.get("label", "")),
                           data.get("label", ""))]
        return []
