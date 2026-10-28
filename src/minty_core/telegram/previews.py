"""A look at what is being asked about, on the phone: the draft video (or picture) of a studio post.

Only a real video or picture that sits directly in Minty's own studio folder is ever sent, to the paired chats, and not on a private
day, so an event cannot be used to ship another file. Off until the studio setting `preview_to_phone` is on."""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Callable

from minty_core.telegram.api import TelegramError

log = logging.getLogger(__name__)
MAX_BYTES = 45 * 1024 * 1024  # Telegram bots may send files up to 50 MB


def _kind(head: bytes, suffix: str) -> str | None:
    if suffix == ".mp4" and head[4:8] == b"ftyp":
        return "video"
    if (suffix == ".png" and head.startswith(b"\x89PNG\r\n\x1a\n")) or (suffix in (".jpg", ".jpeg") and head.startswith(b"\xff\xd8\xff")):
        return "photo"
    return None


class PreviewRelay:
    def __init__(self, gateway, root: Callable[[], Path], enabled: Callable[[], bool]):
        self.gw, self._root, self._enabled = gateway, root, enabled

    def _readable(self, raw: str) -> tuple[Path, str] | None:
        try:
            root = Path(self._root()).expanduser().resolve()
            path = Path(raw)
            if path.is_symlink() or root not in path.resolve().parents:
                return None
            info = os.stat(path)
            if not stat.S_ISREG(info.st_mode) or not 16 <= info.st_size <= MAX_BYTES:
                return None
            with open(path, "rb") as f:
                kind = _kind(f.read(16), path.suffix.lower())
            return (path, kind) if kind else None
        except OSError:
            return None

    async def handle(self, event: dict) -> str:
        gw = self.gw
        if not self._enabled():
            return "off"
        if event.get("private") or gw._private_now() or gw.suspended or not gw.allowed:
            return "private"
        found = self._readable(str(event.get("path") or ""))
        if found is None:
            return "refused"
        path, kind = found
        data, caption = path.read_bytes(), str(event.get("caption") or "A studio draft")[:200]
        for chat in sorted(gw.allowed):
            try:
                await gw.sender.send_media(chat, kind, data, path.name, caption)
            except TelegramError as e:
                log.warning("could not send the preview: %s", e)
        return "sent"

    async def run(self, bus) -> None:
        queue = bus.subscribe({"studio_preview"})
        try:
            while True:
                event = await queue.get()
                try:
                    await self.handle(event)
                except Exception:  # noqa: BLE001 — a failed preview must not stop the loop
                    log.warning("preview relay failed", exc_info=True)
        finally:
            bus.unsubscribe(queue)
