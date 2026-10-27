"""What Minty may send without asking. The user writes this policy; nothing in Minty can change it.

A send is one of three things: `auto` (the user's own rules allow it), `ask` (the user is shown the whole message and
decides) or `refuse` (it is malformed or against a rule that nothing overrides). Automatic means narrow on purpose: one
recipient, who is allowed by address or domain, answering a message that really came from them (checked, not claimed), in
a short note with no links, no other addresses, no secrets and none of the sensitive words, under a daily cap. Anything
else, including every new conversation, is a question. These are plain checks, not a model's confidence, so a persuasive
e-mail cannot talk its way past them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ADDRESS = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_URL = re.compile(r"https?://|www\.", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
DEFAULT_SENSITIVE = ("password", "passcode", "otp", "pin", "bank", "card", "cvv", "aadhaar", "aadhar", "ssn", "passport",
                     "pay", "payment", "transfer", "invoice", "refund", "legal", "lawyer", "contract", "salary", "loan")
MAX_RECIPIENTS = 5
MAX_BODY = 5000
MAX_SUBJECT = 200


def parse_list(value) -> tuple[str, ...]:
    """'a@b.com, @school.edu; c@d.com' -> ('a@b.com', '@school.edu', 'c@d.com'), lower case, no blanks."""
    if not isinstance(value, str):
        return ()
    return tuple(part.strip().lower() for part in re.split(r"[,;\n]", value) if part.strip())


@dataclass(frozen=True)
class Policy:
    allow: tuple[str, ...] = ()  # addresses or @domains that may be answered without asking
    never: tuple[str, ...] = ()  # addresses or @domains that are never written to
    max_per_day: int = 5
    max_chars: int = 800
    sensitive: tuple[str, ...] = DEFAULT_SENSITIVE
    require_authenticated: bool = True


@dataclass(frozen=True)
class Draft:
    to: tuple[str, ...]
    subject: str
    body: str
    reply: bool  # answers a message in the mailbox (the recipient comes from that message, not from the model)
    authenticated: bool | None = None  # the original sender passed SPF/DKIM/DMARC; None: not known
    original_subject: str = ""


@dataclass
class Decision:
    mode: str  # auto | ask | refuse
    reasons: list[str] = field(default_factory=list)


def _matches(address: str, entries: tuple[str, ...]) -> bool:
    address = address.lower()
    return any(address == e or (e.startswith("@") and address.endswith(e)) for e in entries)


def _words(text: str, words: tuple[str, ...]) -> list[str]:
    low = text.lower()
    return [w for w in words if re.search(rf"\b{re.escape(w)}\b", low)]


def decide(policy: Policy, draft: Draft, sent_today: int, has_secret) -> Decision:
    to = tuple(a.strip() for a in draft.to)
    if not to or any(not ADDRESS.match(a) for a in to):
        return Decision("refuse", ["a recipient is not a valid single address"])
    if len(to) > MAX_RECIPIENTS:
        return Decision("refuse", [f"more than {MAX_RECIPIENTS} recipients"])
    if "\n" in draft.subject or "\r" in draft.subject or len(draft.subject) > MAX_SUBJECT:
        return Decision("refuse", ["the subject has a line break or is too long"])
    if not draft.body.strip() or len(draft.body) > MAX_BODY:
        return Decision("refuse", ["the message is empty or too long to send"])
    blocked = [a for a in to if _matches(a, policy.never)]
    if blocked:
        return Decision("refuse", [f"{blocked[0]} is on your never-write list"])

    reasons: list[str] = []
    if len(to) > 1:
        reasons.append("it goes to more than one person")
    for a in to:
        if not _matches(a, policy.allow):
            reasons.append(f"{a} is not on your auto-reply list")
    if not draft.reply:
        reasons.append("it is not a reply to a message from them (a new conversation)")
    if policy.require_authenticated and draft.authenticated is not True:
        reasons.append("I could not confirm the original message really came from that sender")
    hits = _words(f"{draft.subject} {draft.body} {draft.original_subject}", policy.sensitive)
    if hits:
        reasons.append(f"it touches something sensitive ({hits[0]})")
    if len(draft.body) > policy.max_chars:
        reasons.append(f"it is longer than {policy.max_chars} characters")
    if _URL.search(draft.body):
        reasons.append("it contains a link")
    own = {a.lower() for a in to}
    if any(m.group(0).lower() not in own for m in _EMAIL.finditer(draft.body)):
        reasons.append("it mentions another e-mail address")
    if has_secret(f"{draft.subject}\n{draft.body}"):
        reasons.append("it seems to contain a secret or key")
    if sent_today >= policy.max_per_day:
        reasons.append(f"you allow {policy.max_per_day} a day without asking and that many have gone")
    if not policy.allow:
        reasons.append("you have not set anyone to be answered automatically")
    return Decision("ask" if reasons else "auto", reasons)
