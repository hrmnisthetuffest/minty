"""The daily card to the phone (679). Off until `selfcheck.card_push`. Only a real PNG that sits directly inside Minty's own cards
folder is ever sent, so an event cannot be used to ship another file; a private day, a suspended bot or no allowed chat sends nothing."""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Callable

from minty_core.telegram.api import TelegramError

log = logging.getLogger(__name__)
MAX_BYTES = 5 * 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n"


class CardRelay:
    def __init__(self, gateway, root: Callable[[], Path]):
        self.gw, self._root = gateway, root

    def _readable(self, raw: str) -> Path | None:
        try:
            root = Path(self._root()).expanduser().resolve()
            path = Path(raw)
            if path.is_symlink() or path.resolve().parent != root or path.suffix.lower() != ".png":
                return None
            info = os.stat(path)
            if not stat.S_ISREG(info.st_mode) or not 16 <= info.st_size <= MAX_BYTES:
                return None
            with open(path, "rb") as f:
                return path if f.read(8) == PNG else None
        except OSError:
            return None

    async def handle(self, event: dict) -> str:
        gw = self.gw
        if not getattr(gw.store.config, "selfcheck").card_push:
            return "off"
        if event.get("private") or gw._private_now() or gw.suspended or not gw.allowed:
            return "private"
        path = self._readable(str(event.get("path") or ""))
        if path is None:
            return "refused"
        data, caption = path.read_bytes(), str(event.get("caption") or "Your day")[:100]
        for chat in sorted(gw.allowed):
            try:
                await gw.sender.send_media(chat, "photo", data, path.name, caption)
            except TelegramError as e:
                log.warning("could not send the card: %s", e)
        return "sent"

    async def run(self, bus) -> None:
        queue = bus.subscribe({"card"})
        try:
            while True:
                event = await queue.get()
                try:
                    await self.handle(event)
                except Exception:  # noqa: BLE001 — a failed card must not stop the loop
                    log.warning("card relay failed", exc_info=True)
        finally:
            bus.unsubscribe(queue)
