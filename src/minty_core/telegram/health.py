"""Health exports sent to the bot (Hevy, Cronometer, Strava archive, Health Connect) are imported on the laptop.

This hook sits in front of the generic file reader, so an export is never summarised by a model. A file that is not an
export is passed on (its bytes are handed over, not downloaded twice). A file that was plainly meant to be one but
cannot be read is refused here with the reason, rather than passed on as an ordinary document.
"""
from __future__ import annotations

import logging
from typing import Callable

from minty_core.health_import import ImportRejected, import_bytes, looks_like_health, sniff
from minty_core.telegram.api import MAX_DOWNLOAD, TelegramError

log = logging.getLogger(__name__)

EXTENSIONS = (".csv", ".json", ".txt")


class HealthImport:
    def __init__(self, gateway, hub: Callable[[], object | None]):
        self.gw, self._hub = gateway, hub

    def install(self) -> None:
        self.gw.media_hooks.append(self.on_message)

    async def on_message(self, chat_id: int, message: dict) -> bool:
        document = message.get("document")
        hub = self._hub()
        if not document or hub is None:
            return False
        name = str(document.get("file_name") or "")
        if not name.lower().endswith(EXTENSIONS) or int(document.get("file_size") or 0) > MAX_DOWNLOAD:
            return False
        gw = self.gw
        if gw._private_now():
            return False  # the generic reader says "private mode" and downloads nothing
        file_id = str(document.get("file_id"))
        try:
            info = await gw.api.get_file(file_id)
            data = await gw.api.download(str(info.get("file_path")), MAX_DOWNLOAD)
        except TelegramError as e:
            log.warning("health file fetch failed: %s", type(e).__name__)
            return False
        if sniff(data, name) is None and not looks_like_health(data):
            gw.fetched[file_id] = data  # an ordinary file: the generic reader takes it from here
            return False
        try:
            report = import_bytes(hub, data, name)
        except ImportRejected as e:
            await gw.reply(chat_id, f"I couldn't read {name} as a health export: {e}. Nothing was stored.")
            return True
        except Exception:  # noqa: BLE001 — a bug in an adapter must not leak the file onward or crash the gateway
            log.warning("health import failed", exc_info=True)
            await gw.reply(chat_id, f"I couldn't import {name} just now. Nothing was stored.")
            return True
        await gw.reply(chat_id, report.text())
        return True
