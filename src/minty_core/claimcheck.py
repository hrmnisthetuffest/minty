"""Catches "I sent it" when nothing was sent.

Sentences are spoken as they stream, so a false claim has already been heard by the time the answer ends and cannot be taken
back; it can only be corrected straight after. The check is plain text against what the tools really did this turn (a change
is recorded only when a tool of risk class 1 or higher came back without an error), so it costs no model call and is
deliberately narrow: only a first-person claim of a send, a delete or an install, and not a question, a condition, a
negation or something said about the past ("earlier", "yesterday", "already").
"""
from __future__ import annotations

import re

_VERBS = {
    "sent": r"sent|emailed|e-mailed|texted|messaged|posted|forwarded|replied|tweeted",
    "deleted": r"deleted|removed|erased|wiped|trashed|cleared|cancelled|canceled",
    "installed": r"installed|uninstalled|downloaded|uploaded",
}
# words in a tool's own name that show it can do that kind of thing
_BACKED_BY = {
    "sent": ("send", "mail", "message", "post", "reply", "forward", "telegram", "text", "sms", "email", "webhook", "apis",
             "notify", "slack", "chat"),
    "deleted": ("delete", "remove", "erase", "clear", "trash", "wipe", "forget", "purge", "cancel", "drop"),
    "installed": ("install", "download", "upload", "setup"),
}
_ME = r"\bI(?:'ve| have)?\s+(?:(?:just|now|also|successfully|have)\s+)*"
_ACTIVE = {k: re.compile(_ME + f"(?:{v})\\b", re.I) for k, v in _VERBS.items()}
_PASSIVE = {k: re.compile(r"\b(?:has|have) been\s+(?:successfully\s+)?(?:" + v + r")\b|\b(?:is|are|was|were) (?:now |just )(?:"
                          + v + r")\b|(?:'s)\s+(?:now |just )(?:" + v + r")\b", re.I) for k, v in _VERBS.items()}
_NOT_A_CLAIM = re.compile(r"\b(?:earlier|before|previously|yesterday|ago|already|last (?:week|time|night)|if|unless|whether|would|"
                          r"should|could|can|will|might|not|never|unable|going to|about to)\b|n't|\?", re.I)
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


def unbacked_claims(reply: str, done: list[str]) -> list[str]:
    """The kinds of change the reply claims (sent, deleted, installed) that no tool call this turn backs up."""
    heads = [entry.split(" with ", 1)[0].lower() for entry in done]
    claimed: list[str] = []
    for sentence in _SENTENCES.split(reply or ""):
        if not sentence.strip() or _NOT_A_CLAIM.search(sentence):
            continue
        for kind in _VERBS:
            if kind in claimed or not (_ACTIVE[kind].search(sentence) or _PASSIVE[kind].search(sentence)):
                continue
            if not any(word in head for head in heads for word in _BACKED_BY[kind]):
                claimed.append(kind)
    return claimed


def correction(kinds: list[str]) -> str:
    return f"Correction: I haven't actually {' or '.join(kinds)} anything; nothing was done."
