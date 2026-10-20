"""Browser screenshots to the phone (552). Off by default.

The browser tool announces each screenshot on the event bus. Before one leaves the laptop it is read locally; a key,
card, Aadhaar or PAN number withholds it and says so (without repeating it). With no text reader installed it is not
sent either, unless you chose to allow unchecked ones. Only a real PNG/JPEG inside the browser's workspace folder is
ever sent, so an event cannot be used to ship another file. A private day or private page sends nothing, not even a note.
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from minty_core.telegram.api import TelegramError
from minty_core.telegram.intake import check_text

log = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")


class ScreenshotRelay:
    def __init__(self, gateway, scanner, *, ocr: Callable[[Path], Awaitable[str | None]] | None,
                 root: Callable[[], Path], push):
        self.gw, self.scanner, self.ocr, self._root, self.push = gateway, scanner, ocr, root, push

    def _readable(self, raw: str) -> Path | None:
        """The file, if it is a regular PNG/JPEG directly inside the workspace folder and of sane size."""
        try:
            root = Path(self._root()).expanduser().resolve()
            path = Path(raw)
            if path.is_symlink() or path.resolve().parent != root:
                return None
            info = os.stat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES or info.st_size < 16:
                return None
            with open(path, "rb") as f:
                head = f.read(8)
            return path if head.startswith(SIGNATURES) else None
        except OSError:
            return None

    async def handle(self, event: dict) -> str:
        gw = self.gw
        cfg = gw.store.config.telegram
        if not cfg.screenshots:
            return "off"
        if event.get("private") or gw._private_now() or gw.suspended or not gw.allowed:
            return "private"
        path = self._readable(str(event.get("path") or ""))
        if path is None:
            return "refused"
        site = urlparse(str(event.get("url") or "")).hostname or "a page"
        if self.push.is_quiet():
            await self.push.push(f"A screenshot of {site} was taken on the laptop ({path.name}). I held it for quiet hours.",
                                 silent=True)
            return "held"
        text = await self.ocr(path) if self.ocr is not None else None
        if text is None and not cfg.screenshots_unchecked:
            await self.push.push(f"A screenshot of {site} is on the laptop ({path.name}). I didn't send it: I can't "
                                 "check pictures for private details here (tesseract isn't installed).")
            return "unchecked"
        if text is not None:
            why, _ = check_text(self.scanner, text)
            if why:
                await self.push.push(f"A screenshot of {site} ({path.name}) shows {', '.join(why)}, so I kept it on "
                                     "the laptop.")
                return "withheld"
        data = path.read_bytes()
        for chat in sorted(gw.allowed):
            try:
                await gw.sender.send_media(chat, "photo", data, path.name, gw.label(site))
            except TelegramError as e:
                log.warning("could not send a screenshot: %s", e)
        return "sent"

    async def run(self, bus) -> None:
        queue = bus.subscribe({"screenshot"})
        try:
            while True:
                event = await queue.get()
                try:
                    await self.handle(event)
                except Exception:  # noqa: BLE001 — a failed relay must not stop the loop
                    log.warning("screenshot relay failed", exc_info=True)
        finally:
            bus.unsubscribe(queue)
