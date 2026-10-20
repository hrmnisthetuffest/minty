"""Spoken or typed money phrases that need no model: a quick add, a category correction, "that was ...".

Used by the voice command router and by the Telegram chat; both only act when finance.enabled is on.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Callable

from minty_core.finance import Ledger, money
from minty_core.finance_import import parse_quick

_CORRECT = re.compile(r"^(?:categori[sz]e|mark|file|put|set|label)\s+(.+?)\s+(?:as|under|to|in)\s+(.+)$", re.I)
_RULE = re.compile(r"^(?:anything|everything|all|any payment)\s+(?:from|at|to|with)\s+(.+?)\s+(?:is|are|goes? (?:in|under)"
                   r"|should be|counts as)\s+(.+)$", re.I)
_ALWAYS = re.compile(r"^always\s+(?:put|file|count|mark)\s+(.+?)\s+(?:under|in|as)\s+(.+)$", re.I)
_THAT_WAS = re.compile(r"^that (?:was|is)(?: for)?\s+(.+)$", re.I)


class FinanceCommands:
    def __init__(self, ledger: Ledger, today: Callable[[], date] = date.today,
                 enabled: Callable[[], bool] = lambda: True, account: Callable[[], str] = lambda: "Cash"):
        self.ledger, self._today, self._enabled, self._account = ledger, today, enabled, account

    def handle(self, text: str) -> str | None:
        if not self._enabled():
            return None
        t = " ".join((text or "").split()).rstrip(".!?")
        q = parse_quick(t, self._today())
        if q is not None:
            acct = self.ledger.add_account(self._account() or "Cash", kind="cash")
            tid = self.ledger.add_manual(acct, q.day, q.minor, q.description)
            row = next(r for r in self.ledger.transactions(q.day, q.day) if r["id"] == tid)
            return (f"Added {'spend' if q.minor < 0 else 'income'} of {money(abs(q.minor))}: {q.description} "
                    f"({row['category'] or 'uncategorised'}) on {q.day}.")
        m = _RULE.match(t) or _ALWAYS.match(t)
        if m:
            try:
                n = self.ledger.add_rule(m.group(1), m.group(2))
            except ValueError:
                return None
            return (f"Rule saved: {m.group(1).strip().lower()} goes under {m.group(2).strip().lower()}"
                    f"{f', and {n} earlier payment' + ('s' if n != 1 else '') + ' now follow it' if n else ''}.")
        m = _CORRECT.match(t)
        if m:
            match, category = m.group(1).lower(), m.group(2).strip()
            rows = [r for r in self.ledger.transactions() if match in r["description"].lower()]
            if not rows:
                return f"I have no transaction mentioning {match}."
            n = self.ledger.set_category(rows[-1]["id"], category)
            return (f"Done: {n} transaction{'s' * (n != 1)} now {category.lower()}, and I'll file "
                    f"{rows[-1]['merchant'] or 'that'} there from now on.")
        m = _THAT_WAS.match(t)
        if m:
            last = self.ledger.last_transaction_id()
            if last is None:
                return "I haven't recorded anything yet."
            category = m.group(1).strip()
            self.ledger.set_category(last, category)
            return f"Noted: that was {category.lower()}."
        return None
