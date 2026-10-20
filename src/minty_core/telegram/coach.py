"""The coach on the phone: a debrief with one-tap feedback buttons, a pre-workout brief, and the spoken controls.

Events come from the app's coach loop on the bus. Nothing is sent when briefs are off, on a private day or in privacy
mode; quiet hours hold the text (without buttons). The buttons write effort, energy and soreness for that workout.
"""
from __future__ import annotations

import logging
from typing import Callable

from minty_core.coach.commands import CoachCommands
from minty_core.telegram.api import TelegramError

log = logging.getLogger(__name__)

QUESTIONS = (("e", "effort", ("easy", "right", "hard")), ("n", "energy", ("low", "ok", "high")),
             ("s", "soreness", ("none", "some", "lots")))
KIND = {letter: name for letter, name, _ in QUESTIONS}


def keyboard(workout_id: int) -> dict:
    return {"inline_keyboard": [[{"text": f"{name.capitalize()}: {v}", "callback_data": f"cf:{workout_id}:{k}:{v}"}
                                 for v in values] for k, name, values in QUESTIONS]}


class CoachDelivery:
    def __init__(self, gateway, push, hub: Callable[[], object | None]):
        self.gw, self.push, self._hub = gateway, push, hub

    def install(self) -> None:
        self.gw.callbacks.append(self.on_callback)
        self.gw.text_hooks.append(self.on_text)

    # ---- out --------------------------------------------------------------------------------------
    async def handle(self, event: dict) -> str:
        gw = self.gw
        cfg = gw.store.config
        if not (cfg.coach.enabled and cfg.coach.briefs):
            return "off"
        if gw._private_now() or gw.suspended or not gw.allowed:
            return "private"
        text = str(event.get("text") or "")
        if not text:
            return "empty"
        if event.get("kind") == "debrief" and not self.push.is_quiet():
            for chat in sorted(gw.allowed):
                try:
                    await gw.sender.send(chat, gw.label(text), reply_markup=keyboard(int(event["workout_id"])))
                except TelegramError as e:
                    log.warning("could not send a debrief: %s", e)
            return "sent"
        sent = await self.push.push(text, silent=True)
        return "sent" if sent else "held"

    async def run(self, bus) -> None:
        queue = bus.subscribe({"coach"})
        try:
            while True:
                event = await queue.get()
                try:
                    await self.handle(event)
                except Exception:  # noqa: BLE001 — a failed delivery must not stop the loop
                    log.warning("coach delivery failed", exc_info=True)
        finally:
            bus.unsubscribe(queue)

    # ---- in ---------------------------------------------------------------------------------------
    async def on_callback(self, chat_id: int, callback: dict, data: str) -> bool:
        if not data.startswith("cf:"):
            return False
        hub = self._hub()
        try:
            _, wid, letter, value = data.split(":", 3)
            kind = KIND[letter]
            if value not in dict((n, v) for _, n, v in QUESTIONS)[kind] or hub is None:
                raise KeyError(letter)
            hub.save_feedback(int(wid), kind, value)
        except (ValueError, KeyError, Exception):  # noqa: BLE001 — a stale or bad button is just "out of date"
            await self.gw.api.answer_callback(callback.get("id", ""), "That one is out of date.")
            return True
        await self.gw.api.answer_callback(callback.get("id", ""), "Noted.")
        return True

    async def on_text(self, chat_id: int, text: str) -> bool:
        hub = self._hub()
        if hub is None or not self.gw.store.config.coach.enabled:
            return False
        reply = CoachCommands(hub).handle(text)
        if reply is None:
            return False
        await self.gw.reply(chat_id, reply)
        return True
