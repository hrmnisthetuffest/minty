"""Approvals from the phone: inline buttons bound to the exact action, never for class 3 (unless you opted in with a
passphrase).

A question is offered here only when the request behind it was made on Telegram (`ApprovalStore.origin_of`); one raised on the laptop, whether a
background job is waiting on it or it is being asked out loud, stays on the laptop.

A button carries `ap:<id>:<first 8 hex of the action hash>:<y|n>`. Pressing it decides that approval only if the id
exists, is still pending (not expired, not answered) and the hash prefix matches, so a button for one action cannot
approve another and a stale button says so.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Callable

LAPTOP_ONLY = "This one needs your yes on the laptop. To approve big ones from here: run minty telegram-passphrase."
BIG_ONE = ("It is a big one (money, keys, deleting or sending), so it needs your yes on the laptop. To approve big ones from here, run "
           "`minty telegram-passphrase` on the laptop and turn on telegram.class3_passphrase.")
STALE = "That request has expired or was already answered."
PASSPHRASE_TRIES = 3
PASSPHRASE_WINDOW_S = 600.0
PASSPHRASE_WAIT_S = 120.0


def button_data(approval, yes: bool) -> str:
    return f"ap:{approval.id}:{approval.hash[:8]}:{'y' if yes else 'n'}"


def keyboard(approval) -> dict:
    return {"inline_keyboard": [[{"text": "Approve", "callback_data": button_data(approval, True)},
                                 {"text": "Deny", "callback_data": button_data(approval, False)}]]}


class Passphrase:
    """The optional second factor for class-3 approvals: a scrypt hash on disk, never the words."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock
        self._fails: list[float] = []

    def set(self, words: str) -> None:
        salt = os.urandom(16)
        digest = hashlib.scrypt(words.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"salt": salt.hex(), "hash": digest.hex()}, f)

    def exists(self) -> bool:
        return self.path.exists()

    def check(self, words: str) -> bool:
        now = self._clock()
        self._fails = [t for t in self._fails if now - t < PASSPHRASE_WINDOW_S]
        if len(self._fails) >= PASSPHRASE_TRIES:
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            digest = hashlib.scrypt(words.encode(), salt=bytes.fromhex(data["salt"]), n=2**14, r=8, p=1, dklen=32)
            ok = hmac.compare_digest(digest.hex(), data["hash"])
        except (OSError, ValueError, KeyError):
            ok = False
        if not ok:
            self._fails.append(now)
        return ok


class ApprovalBridge:
    def __init__(self, gateway, approvals, passphrase: Passphrase | None = None,
                 clock: Callable[[], float] = time.time):
        self.gw, self.approvals, self.passphrase, self._clock = gateway, approvals, passphrase, clock
        self._offered: set[int] = set()
        self._waiting: dict[int, tuple[int, float]] = {}  # chat id -> (approval id, asked at): expecting a passphrase
        gateway.callbacks.append(self.on_callback)
        gateway.handlers["/approvals"] = self.list_pending
        gateway.text_hooks.append(self.on_text)

    # ---- offering ---------------------------------------------------------------------------------
    def _big_ones_here(self) -> bool:
        return self.passphrase is not None and bool(self.gw.store.config.telegram.class3_passphrase) and self.passphrase.exists()

    async def _show(self, chat: int, ap, lead: str) -> None:
        """The question with its two buttons; a big one that cannot be approved here is said plainly (no button that only refuses)."""
        if ap.risk >= 3 and not self._big_ones_here():
            await self.gw.sender.send(chat, self.gw.label(f"{lead} on the laptop: {ap.question}\n{BIG_ONE}"))
        else:
            await self.gw.sender.send(chat, self.gw.label(f"{lead}: {ap.question}"), reply_markup=keyboard(ap))

    async def offer_new(self) -> int:
        """Send every open question that was asked on Telegram, once, to every paired chat. Nothing asked on the laptop is sent."""
        sent = 0
        for ap in self.approvals.pending():
            if ap.id in self._offered or self.approvals.origin_of(ap.id) != "telegram":
                continue
            self._offered.add(ap.id)
            for chat in sorted(self.gw.allowed):
                await self._show(chat, ap, "Approval needed")
                sent += 1
        return sent

    async def list_pending(self, chat_id: int, _args: str) -> str | None:
        """/approvals: you asked here, so the ones a background job waits on are shown too, wherever they started."""
        waiting = [a for a in self.approvals.pending() if a.job_id is not None or self.approvals.origin_of(a.id) == "telegram"]
        if not waiting:
            return "Nothing is waiting for your approval."
        for ap in waiting[:5]:
            await self._show(chat_id, ap, "Waiting")
        return None

    # ---- pressing a button ------------------------------------------------------------------------
    async def on_callback(self, chat_id: int, callback: dict, data: str) -> bool:
        if not data.startswith("ap:"):
            return False
        cid = callback.get("id", "")
        try:
            _p, ident, prefix, answer = data.split(":")
            ident = int(ident)
        except ValueError:
            await self.gw.api.answer_callback(cid, STALE)
            return True
        rec = self.approvals.get(ident)
        if rec is None or rec.status != "pending" or not rec.hash.startswith(prefix) or len(prefix) != 8:
            await self.gw.api.answer_callback(cid, STALE)
            return True
        message = callback.get("message") or {}
        if answer == "n":
            self.approvals.decide(ident, False, via="telegram")
            await self._done(chat_id, message, cid, "Denied.")
            return True
        if rec.risk >= 3:
            if self.passphrase is None or not self.gw.store.config.telegram.class3_passphrase \
                    or not self.passphrase.exists():
                await self.gw.api.answer_callback(cid, LAPTOP_ONLY, alert=True)
                return True
            self._waiting[chat_id] = (ident, self._clock())
            await self.gw.api.answer_callback(cid, "Reply with your passphrase.")
            await self.gw.sender.send(chat_id, self.gw.label("Reply with your passphrase to approve this. "
                                                             "It is not stored and the message is not kept."))
            return True
        if self.approvals.decide(ident, True, via="telegram"):
            await self._done(chat_id, message, cid, "Approved.")
        else:
            await self.gw.api.answer_callback(cid, STALE)
        return True

    async def _done(self, chat_id: int, message: dict, cid: str, text: str) -> None:
        await self.gw.api.answer_callback(cid, text)
        if message.get("message_id"):
            try:
                await self.gw.sender.edit(chat_id, message["message_id"], self.gw.label(text))
            except Exception:  # noqa: BLE001 — the decision is made; a failed edit is cosmetic
                pass

    # ---- the passphrase reply (class 3, opt-in) -----------------------------------------------------
    async def on_text(self, chat_id: int, text: str) -> bool:
        waiting = self._waiting.get(chat_id)
        if waiting is None:
            return False
        ident, asked = waiting
        del self._waiting[chat_id]
        if self._clock() - asked > PASSPHRASE_WAIT_S:
            await self.gw.reply(chat_id, STALE)
            return True
        rec = self.approvals.get(ident)
        if rec is None or rec.status != "pending":
            await self.gw.reply(chat_id, STALE)
        elif self.passphrase.check(text.strip()):
            self.approvals.decide(ident, True, via="telegram+passphrase")
            await self.gw.reply(chat_id, "Approved.")
        else:
            await self.gw.reply(chat_id, "That wasn't it. Press Approve again to retry.")
        return True
