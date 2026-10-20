"""Page helpers that only read what the browser already shows: a spoken "what's clickable here" (575) and the login
hand-over (573) that waits while you log in yourself. Neither types, clicks or submits anything."""
from __future__ import annotations

import re
import time
from typing import Callable

from minty_core.webpage import elements

_SECRET = re.compile(r"pass(?:word|code)|\bpin\b|one[- ]?time|\botp\b|verification code|security code", re.I)
_TEXT = {"textbox", "searchbox", "spinbutton"}
GROUPS = [("button", "buttons", "button", {"button", "menuitem"}),
          ("link", "links", "link", {"link"}),
          ("text field", "text fields", "text field", _TEXT),
          ("dropdown", "dropdowns", "dropdown", {"combobox", "listbox"}),
          ("tick box", "tick boxes", "tick box", {"checkbox", "radio", "switch"}),
          ("tab", "tabs", "tab", {"tab"})]
SHOWN = 5


def asks_for_login(nodes) -> bool:
    return any(n.role in _TEXT and _SECRET.search(n.label or "") for n in nodes)


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def clickable_summary(nodes, title: str, url: str, risky: Callable = lambda n: False) -> str:
    found = elements(nodes)
    head = (title or url or "This page").strip().rstrip(".")
    if not found:
        return f"{head}. I don't see anything to click on this page."
    parts: list[str] = []
    popup = [n for n in found if n.in_dialog and n.role != "dialog"]
    if popup:
        parts.append("In a pop-up: " + ", ".join(n.label[:40] for n in popup[:SHOWN] if n.label) + ".")
    rest = [n for n in found if n not in popup]
    for one, many, _name, roles in GROUPS:
        group = [n for n in rest if n.role in roles and n.label]
        if not group:
            continue
        labels = [n.label[:40] for n in group]
        text = f"{_count(len(group), one, many)}: {', '.join(labels[:SHOWN])}"
        if len(labels) > SHOWN:
            text += f" and {len(labels) - SHOWN} more"
        parts.append(text + ".")
    careful = [n.label[:40] for n in found if n.label and risky(n)]
    if careful:
        parts.append("Careful with: " + ", ".join(careful[:SHOWN]) + ".")
    return f"{head}. " + " ".join(parts)


class HandOver:
    """You log in; Minty waits. It never types or reads a password and gives up after `patience_s`."""

    def __init__(self, clock: Callable[[], float] = time.time, patience_s: float = 600.0):
        self._clock, self.patience_s = clock, patience_s
        self.active: dict | None = None

    def start(self, url: str, nodes) -> str:
        if not asks_for_login(nodes):
            raise ValueError("I see no login box on this page.")
        self.active = {"url": url, "since": self._clock()}
        return ("This page wants a login. I will not type your password or code; log in yourself in the browser "
                "window and say 'I'm in', or I will notice when the password box is gone.")

    def check(self, url: str, nodes) -> tuple[bool, str]:
        if self.active is None:
            return True, "No hand-over is waiting."
        if not asks_for_login(nodes):
            self.active = None
            return True, "You are in. I will carry on."
        if self._clock() - self.active["since"] > self.patience_s:
            self.active = None
            return True, "I stopped waiting for the login. Say so when you want to try again."
        return False, "Still waiting for you to log in."

    def cancel(self) -> None:
        self.active = None
