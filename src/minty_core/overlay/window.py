"""The Tk skin of the HUD: five labels in a small borderless always-on-top window in a screen corner. Everything it
decides comes from `OverlayModel`; this only draws a Frame and shows or hides itself. A separate process
(`minty-core overlay`) so a problem here can never touch the voice."""
from __future__ import annotations

import logging
from typing import Callable

from minty_core.overlay.model import Frame, OverlayModel
from minty_core.overlay.xwin import ActiveWindowReader

log = logging.getLogger(__name__)

TITLE = "Minty HUD"
COLOURS = {"idle": "#8a8f98", "listening": "#4ea3ff", "speaking": "#6fdc8c", "processing": "#ffd166",
           "thinking": "#ffd166", "executing": "#ffd166", "error": "#ff6b6b", "live": "#c792ea"}


class HudWindow:
    def __init__(self, model: OverlayModel, reader: ActiveWindowReader, fetch: Callable[[], dict | None], *,
                 poll_ms: int = 500, corner: str = "bottom-right", opacity: float = 0.92, never_map: bool = False,
                 tk_root=None):
        import tkinter as tk

        self.model, self.reader, self.fetch = model, reader, fetch
        self.poll_ms, self.corner, self.never_map = poll_ms, corner, never_map
        self.root = tk_root or tk.Tk(className="minty-overlay")
        self.root.withdraw()
        self.root.title(TITLE)
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", opacity)
        except Exception:  # noqa: BLE001 — a compositor-less session may refuse
            log.debug("could not set HUD attributes", exc_info=True)
        self.root.configure(bg="#14161a")
        box = {"bg": "#14161a", "fg": "#e6e6e6", "anchor": "w", "justify": "left", "wraplength": 360, "padx": 10}
        self.state_label = tk.Label(self.root, font=("Sans", 10, "bold"), **{**box, "pady": 2})
        self.caption_label = tk.Label(self.root, font=("Sans", 11), **{**box, "pady": 2})
        self.timers_label = tk.Label(self.root, font=("Sans", 12, "bold"), **{**box, "pady": 2})
        self.notice_label = tk.Label(self.root, font=("Sans", 10), **{**box, "fg": "#ffd166", "pady": 2})
        # the notes list is the last block, so it sits at the bottom of the window (where a music card would go below it)
        self.notes_label = tk.Label(self.root, font=("Sans", 10), **{**box, "fg": "#9ad1ff", "pady": 4})
        for label in (self.state_label, self.caption_label, self.timers_label, self.notice_label, self.notes_label):
            label.pack(fill="x")
        self._shown = False

    def draw(self, frame: Frame) -> None:
        self.state_label.configure(text=f"Minty · {frame.state}", fg=COLOURS.get(frame.state, "#e6e6e6"))
        self.caption_label.configure(text=frame.caption)
        self.timers_label.configure(text="\n".join(frame.timers))
        self.notice_label.configure(text=frame.notice)
        notes = "✎ Noting\n" + "\n".join(f"• {n}" for n in frame.notes) if frame.noting else ""
        self.notes_label.configure(text=notes)
        for label in (self.caption_label, self.timers_label, self.notice_label, self.notes_label):
            label.pack_forget()  # repacked in order, so the notes stay last
        for label, text in ((self.caption_label, frame.caption), (self.timers_label, "\n".join(frame.timers)),
                            (self.notice_label, frame.notice), (self.notes_label, notes)):
            if text:
                label.pack(fill="x")
        if self.never_map:
            return
        if frame.visible and not self._shown:
            self.root.deiconify()
            self._shown = True
        elif not frame.visible and self._shown:
            self.root.withdraw()
            self._shown = False
        if self._shown:
            self.root.update_idletasks()
            w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            x = 24 if "left" in self.corner else sw - w - 24
            y = 48 if "top" in self.corner else sh - h - 64
            self.root.geometry(f"+{x}+{y}")

    def update(self) -> Frame:
        snap = None
        try:
            snap = self.fetch()
        except Exception:  # noqa: BLE001 — the core being down just empties the HUD
            log.debug("overlay fetch failed", exc_info=True)
        self.model.apply(snap or {})
        frame = self.model.frame(self.reader.read())
        self.draw(frame)
        return frame

    def run(self) -> None:
        def tick():
            try:
                self.update()
            finally:
                self.root.after(self.poll_ms, tick)

        self.root.after(0, tick)
        self.root.mainloop()
