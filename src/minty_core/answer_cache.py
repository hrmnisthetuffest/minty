"""Short-lived cache for idempotent lookups (weather, web search): the same question within minutes is answered
from memory instead of another call. Never stores anything personal: lookups that mention the user ("my ...") or
hold an e-mail address, phone or account number are not cached, and nothing is cached during a private turn.
In memory only; it is gone on restart.
"""
from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from typing import Callable

from minty_core.trace import turn_fields

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_DIGITS = re.compile(r"(?:\+?\d[\d\s-]{7,}\d)")
_FIRST_PERSON = re.compile(r"\b(?:my|mine|me|i|i'm|our|ours|us)\b", re.IGNORECASE)


def personal(args: dict) -> bool:
    text = " ".join(str(v) for v in args.values())
    return bool(_EMAIL.search(text) or _DIGITS.search(text) or _FIRST_PERSON.search(text))


def _key(tool: str, args: dict) -> str:
    norm = {k: " ".join(str(v).lower().split()) if isinstance(v, str) else v for k, v in sorted(args.items())}
    return tool + "|" + json.dumps(norm, sort_keys=True, default=str)


class AnswerCache:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, max_items: int = 200):
        self._clock, self._max = clock, max_items
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def get(self, tool: str, args: dict) -> str | None:
        key = _key(tool, args)
        hit = self._items.get(key)
        if hit is None:
            return None
        if hit[0] <= self._clock():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return hit[1]

    def put(self, tool: str, args: dict, result: str, ttl: float) -> None:
        if ttl <= 0 or personal(args) or turn_fields().get("private"):
            return
        key = _key(tool, args)
        self._items[key] = (self._clock() + ttl, result)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)
