"""How hard a request is, decided without a model: chit-chat and single-tool turns can go to a provider's small
model (its own free quota), planning, writing and multi-step work to the large one."""
from __future__ import annotations

import re
from typing import Literal

from minty_core.llm.types import Message

_WORDS = re.compile(r"[a-z0-9']+")
# words that mean real thinking, writing or a multi-step task
_LARGE = frozenset("""plan planning research compare comparison analyse analyze analysis explain why how write draft
essay story poem letter email code coding debug fix summarise summarize summary translate calculate solve prove
design strategy itinerary review pros cons step steps find cheapest best recommend choose decide book order buy
schedule organise organize outline teach difference""".split())
_STEPS = re.compile(r"\b(?:and then|then|after that|afterwards|followed by)\b")
MAX_SMALL_WORDS = 25


def classify(messages: list[Message]) -> Literal["small", "large"]:
    """'small' or 'large' for the request this model call is answering (the last user message), and how many
    tool results have come back since (a chain of tools is a multi-step task)."""
    last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
    if last_user < 0:
        return "large"
    if sum(1 for m in messages[last_user + 1:] if m.role == "tool") >= 2:
        return "large"
    text = (messages[last_user].content or "").lower()
    words = _WORDS.findall(text)
    if len(words) > MAX_SMALL_WORDS or _STEPS.search(text) or _LARGE.intersection(words):
        return "large"
    return "small"


# words that mean the work is hard (code, proofs, plans, comparisons), not just a bit more than chit-chat
_HARD = frozenset("""plan planning research compare comparison analyse analyze analysis code coding debug prove solve
design strategy itinerary essay outline review calculate translate""".split())
MAX_MEDIUM_WORDS = 60


def classify_level(messages: list[Message]) -> Literal["small", "medium", "large"]:
    """classify() with its 'large' split in two: 'medium' (explain, write a short note, find something) and 'large'
    (hard words, several steps, a long request, a chain of tools). Providers that have one small model treat
    'medium' and 'large' alike; the Claude ladder uses all three."""
    if classify(messages) == "small":
        return "small"
    last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
    if last_user < 0 or sum(1 for m in messages[last_user + 1:] if m.role == "tool") >= 2:
        return "large"
    text = (messages[last_user].content or "").lower()
    words = _WORDS.findall(text)
    if len(words) > MAX_MEDIUM_WORDS or _STEPS.search(text) or _HARD.intersection(words):
        return "large"
    return "medium"
