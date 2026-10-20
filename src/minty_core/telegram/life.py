"""The daily check-in (two questions, buttons) and the weekly review on the phone. Pushes honour quiet hours; nothing
is sent on a private day or when these are switched off."""
from __future__ import annotations

import logging
from typing import Callable

from minty_core.life import save_checkin
from minty_core.telegram.api import TelegramError

log = logging.getLogger(__name__)

ENERGY = ("low", "ok", "high")


def keyboard(day: str) -> dict:
    return {"inline_keyboard": [
        [{"text": f"Energy: {v}", "callback_data": f"ck:{day}:e:{v}"} for v in ENERGY],
        [{"text": "Today's priority: done", "callback_data": f"ck:{day}:p:y"},
         {"text": "not yet", "callback_data": f"ck:{day}:p:n"}]]}


class LifeDelivery:
    def __init__(self, gateway, push, hub: Callable[[], object | None]):
        self.gw, self.push, self._hub = gateway, push, hub

    def install(self) -> None:
        self.gw.callbacks.append(self.on_callback)

    async def handle(self, event: dict) -> str:
        gw, cfg = self.gw, self.gw.store.config.life
        kind = event.get("kind")
        if kind == "checkin" and not cfg.checkins or kind == "review" and not cfg.review_push:
            return "off"
        if gw._private_now() or gw.suspended or not gw.allowed:
            return "private"
        if kind == "checkin":
            if self.push.is_quiet():
                return "held"
            for chat in sorted(gw.allowed):
                try:
                    await gw.sender.send(chat, gw.label("Quick check-in: how was your energy today, and did you "
                                                        "get to your main thing?"),
                                         reply_markup=keyboard(str(event["day"])))
                except TelegramError as e:
                    log.warning("could not send the check-in: %s", e)
            return "sent"
        sent = await self.push.push(str(event.get("text") or ""), silent=True)
        return "sent" if sent else "held"

    async def run(self, bus) -> None:
        queue = bus.subscribe({"life"})
        try:
            while True:
                event = await queue.get()
                try:
                    await self.handle(event)
                except Exception:  # noqa: BLE001
                    log.warning("life delivery failed", exc_info=True)
        finally:
            bus.unsubscribe(queue)

    async def on_callback(self, chat_id: int, callback: dict, data: str) -> bool:
        if not data.startswith("ck:"):
            return False
        hub = self._hub()
        try:
            _, day, which, value = data.split(":", 3)
            if hub is None:
                raise ValueError
            if which == "e" and value in ENERGY:
                save_checkin(hub, day, energy=value)
            elif which == "p" and value in ("y", "n"):
                save_checkin(hub, day, priority_done=value == "y")
            else:
                raise ValueError
        except (ValueError, KeyError):
            await self.gw.api.answer_callback(callback.get("id", ""), "That one is out of date.")
            return True
        await self.gw.api.answer_callback(callback.get("id", ""), "Noted.")
        return True
