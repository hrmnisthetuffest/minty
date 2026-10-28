"""Words from outside that end up somewhere that matters.

A creator's name is added to the caption of a public post, and titles and notes are read back by the model in tool results. Both are
written by strangers, so they are cleaned before they are kept: one plain line, letters and a little punctuation, no addresses, no
@mentions or #tags, no markup, no bidi tricks, a bounded length, and nothing that gives a model orders.
"""
from __future__ import annotations

import re
import unicodedata

from minty_core.provenance import looks_like_injection

_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|net|org|io|co|me|ly|gg|tv|xyz|ru|cn)\b\S*")
_TAG = re.compile(r"<[^>]*>")
_SIGILS = re.compile(r"[@#]")  # the name stays, the mention does not: "@bob" is "bob" in a caption
_NAME_OK = re.compile(r"[^\w\s.'’&()\-]")
_LINE_OK = re.compile(r"[^\w\s.,'’&()\-:;!?/]")


def _scrub(raw, max_len: int, outside: re.Pattern, fallback: str) -> str:
    if raw is None:
        return fallback
    text = unicodedata.normalize("NFKC", str(raw))
    if looks_like_injection(text):
        return fallback
    text = re.sub(r"\s+", " ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")  # control and format characters (bidi overrides ...)
    for pattern in (_TAG, _URL, _SIGILS, outside):
        text = pattern.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -,;:/")
    return text[:max_len].rstrip(" -,;:/") or fallback


def clean_credit_name(raw, fallback: str = "a creator") -> str:
    """A person's or a channel's name, for a credit line."""
    return _scrub(raw, 60, _NAME_OK, fallback)


def clean_title(raw, max_len: int = 80, fallback: str = "untitled") -> str:
    """A title, a tag list or a note: one short plain line."""
    return _scrub(raw, max_len, _LINE_OK, fallback)
