"""Instant answers: the time, maths, conversions, dice, facts and small talk, answered at once with no model call.

A request is answered here only when the whole of it is a known question (the rules are anchored); anything else, or any
rule that fails, goes on to the model as usual. Nothing here uses the network. `docs/instant-answers.md` lists everything it
understands and is written from the rules, so it cannot go stale.
"""
from __future__ import annotations

import logging

from minty_core.instant import calc, chat, clock, facts, fun, system, units
from minty_core.instant.base import Env, Hit, Query, Rule
from minty_core.instant.text import clean

log = logging.getLogger(__name__)
MAX_WORDS = 24
MAX_CHARS = 400  # a whole spoken request is short; a huge single word is never one of the known questions
RULES: tuple[Rule, ...] = (*chat.RULES, *clock.RULES, *system.RULES, *calc.RULES, *units.RULES, *facts.RULES, *fun.RULES)
CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(r.category for r in RULES))


class Instant:
    def __init__(self, env: Env | None = None, *, skip=(), rules=None):
        self.env = env or Env()
        self._rules = tuple(RULES if rules is None else rules)
        self._skip = frozenset(skip)

    def answer(self, text: str, skip=None) -> Hit | None:
        """The answer to a request that is wholly one of the known questions, or None. `skip` overrides the categories to leave out."""
        skipped = self._skip if skip is None else frozenset(skip)
        if not text or len(text) > MAX_CHARS or "\n" in text.strip() or len(text.split()) > MAX_WORDS + 6:
            return None
        q = Query.of(text, self.env)
        if not q.cmd or len(q.cmd.split()) > MAX_WORDS:
            return None
        for rule in self._rules:
            if rule.category in skipped:
                continue
            try:
                reply = rule.run(q)
            except Exception:  # noqa: BLE001 - one broken rule must never cost the user an answer
                log.warning("instant rule %r failed", rule.name, exc_info=True)
                continue
            if reply:
                return Hit(reply, rule.name, rule.category)
        return None


__all__ = ["Env", "Hit", "Instant", "Query", "Rule", "RULES", "CATEGORIES", "clean"]
