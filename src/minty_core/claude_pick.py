"""Asking for a Claude model by name: "use opus on high effort to explain tides" (this request), or just
"use opus on max effort" (the next request). Opus, and any model you name, is used only this way; everyday turns
follow the ladder (llm/claude_cli.py, config.claude_brain_providers)."""
from __future__ import annotations

import re
from typing import NamedTuple

EFFORTS = {"low": "low", "medium": "medium", "high": "high", "extra high": "xhigh", "xhigh": "xhigh",
           "max": "max", "maximum": "max"}
_EFFORT = "|".join(sorted((re.escape(k) for k in EFFORTS), key=len, reverse=True))
_ASK = re.compile(
    rf"^\s*(?:hey\s+|ok\s+|okay\s+)?(?:please\s+)?(?:use|switch\s+to|try|ask)\s+(opus|sonnet|haiku)\b"
    rf"(?:\s+(?:on|at|with|in)\s+({_EFFORT})(?:\s+effort)?(?:\s+mode)?)?\s*[,:;.\-]*\s*(?:(?:to|for|and)\s+)?(.*)$",
    re.IGNORECASE | re.DOTALL)


class Pick(NamedTuple):
    model: str
    effort: str | None  # None: the program's own default


def parse_pick(text: str) -> tuple[Pick | None, str]:
    """(the model and effort asked for, the rest of the request), or (None, text) for an ordinary sentence."""
    m = _ASK.match(text or "")
    if not m:
        return None, text
    model, effort, rest = m.group(1).lower(), m.group(2), m.group(3).strip()
    return Pick(model, EFFORTS[effort.lower()] if effort else None), rest
