"""Money from the phone: a typed spend, a forwarded bank SMS, a statement file, a category correction.

All of it is read by code on this laptop and written to the local ledger; nothing is shown to a model. It only acts
when finance.enabled is on, and (like every file) nothing is downloaded on a private day.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from minty_core.finance import money
from minty_core.finance_commands import FinanceCommands
from minty_core.finance_import import ImportRejected, import_csv, looks_like_statement, parse_sms
from minty_core.telegram.api import MAX_DOWNLOAD, TelegramError

log = logging.getLogger(__name__)


class FinanceTelegram:
    def __init__(self, gateway, ledger: Callable[[], object | None], today: Callable[[], date] = date.today):
        self.gw, self._ledger, self._today = gateway, ledger, today

    def install(self) -> None:
        self.gw.text_hooks.append(self.on_text)
        self.gw.media_hooks.append(self.on_message)
        self.gw.forward_hooks.append(self.on_forward)

    def _on(self):
        cfg = self.gw.store.config.finance
        return self._ledger() if cfg.enabled else None

    def _account(self) -> str:
        return self.gw.store.config.finance.default_account or "Cash"

    async def on_text(self, chat_id: int, text: str) -> bool:
        ledger = self._on()
        if ledger is None:
            return False
        reply = FinanceCommands(ledger, self._today, account=self._account).handle(text)
        if reply is None:
            return False
        await self.gw.reply(chat_id, reply)
        return True

    async def on_forward(self, chat_id: int, text: str, message: dict) -> bool:
        ledger = self._on()
        if ledger is None:
            return False
        parsed = parse_sms(text, self._today())
        if parsed is None:
            return False
        acct = ledger.add_account(self._account(), kind="cash")
        for a in ledger.accounts():  # an account whose name carries the last four digits gets it
            if parsed.account_hint and parsed.account_hint[-4:] in a["name"]:
                acct = a["id"]
        added = ledger.add_batch(acct, [(parsed.day, parsed.minor, parsed.description)], source="sms")
        await self.gw.reply(chat_id, (f"Recorded {money(parsed.minor)}: {parsed.description} on {parsed.day}."
                                      if added.added else "I already have that one."))
        return True

    async def on_message(self, chat_id: int, message: dict) -> bool:
        document = message.get("document")
        ledger = self._on()
        if not document or ledger is None:
            return False
        name = str(document.get("file_name") or "")
        if not name.lower().endswith((".csv", ".txt", ".tsv")) or int(document.get("file_size") or 0) > MAX_DOWNLOAD:
            return False
        gw = self.gw
        if gw._private_now():
            return False
        file_id = str(document.get("file_id"))
        try:
            info = await gw.api.get_file(file_id)
            data = await gw.api.download(str(info.get("file_path")), MAX_DOWNLOAD)
        except TelegramError as e:
            log.warning("statement fetch failed: %s", type(e).__name__)
            return False
        if not looks_like_statement(data):
            gw.fetched[file_id] = data  # not a statement: the ordinary file reader takes it
            return False
        caption = str(message.get("caption") or "").strip()
        account = caption[8:].strip() if caption.lower().startswith("account ") else self._account()
        try:
            report = import_csv(ledger, ledger.add_account(account or self._account()), data)
        except ImportRejected as e:
            await gw.reply(chat_id, f"I couldn't read {name} as a statement: {e}. Nothing was stored.")
            return True
        none = sum(1 for t in ledger.transactions() if not t["category"])
        tail = (f" {none} have no category yet; say 'categorize <name> as <category>' and I'll learn it."
                if none else "")
        await gw.reply(chat_id, report.text() + tail)
        return True
