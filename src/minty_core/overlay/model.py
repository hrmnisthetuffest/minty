"""What the HUD shows and when. Pure logic: the Tk window only draws a `Frame`.

Visible only while another window is active (your rule): the Minty terminal, the panel and the HUD itself count as
Minty being focused, and not knowing what is focused counts as not showing."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from minty_core.overlay.xwin import ActiveWindow

MINTY_HINTS = ("minty",)
TERMINAL_CLASSES = ("terminal", "konsole", "kitty", "alacritty", "xterm", "tilix", "terminator", "wezterm", "foot",
                    "urxvt", "st-256color")
CAPTION_FADE_S = 6.0
NOTICE_FOR_S = 8.0
MAX_TIMERS = 3


@dataclass
class Frame:
    visible: bool
    state: str = "idle"
    caption: str = ""
    notice: str = ""
    timers: list[str] = field(default_factory=list)
    noting: bool = False  # "note for me" is on
    notes: list[str] = field(default_factory=list)  # the latest notes, oldest first (only while noting)


def clock_text(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


class OverlayModel:
    def __init__(self, enabled: bool = True, clock: Callable[[], float] = time.monotonic,
                 hints: tuple[str, ...] = MINTY_HINTS):
        self.enabled, self._clock, self._hints = enabled, clock, tuple(h.lower() for h in hints)
        self._snap: dict = {}

    def apply(self, snapshot: dict) -> None:
        self._snap = snapshot or {}

    def minty_is_focused(self, w: ActiveWindow) -> bool:
        title, cls = w.title.lower(), w.wm_class.lower()
        if "minty-overlay" in cls or "hud" in title and any(h in title for h in self._hints):
            return True
        if any(h in title for h in self._hints):
            return True  # the Minty terminal ("minty run"), the panel tab, the HUD
        return False

    def frame(self, active: ActiveWindow | None) -> Frame:
        visible = self.enabled and active is not None and not self.minty_is_focused(active)
        s = self._snap
        partial = f"{s.get('partial', '')}{s.get('tentative', '')}".strip()
        caption = partial
        if not caption and s.get("sentence") and float(s.get("sentence_age_s", 99)) <= CAPTION_FADE_S:
            caption = str(s["sentence"])
        notice = str(s.get("notice", "")) if float(s.get("notice_age_s", 99)) <= NOTICE_FOR_S else ""
        timers = sorted(s.get("timers", []), key=lambda t: (t.get("kind") != "recording", t.get("kind") != "study",
                                                              t.get("remaining_s", 0)))  # REC first, always
        lines = [f"{t['label']} {clock_text(t['remaining_s'])}" for t in timers[:MAX_TIMERS]]
        noting = bool(s.get("noting"))
        notes = [str(n) for n in s.get("notes", [])] if noting else []
        return Frame(visible, str(s.get("state", "idle")), caption, notice, lines, noting, notes)
