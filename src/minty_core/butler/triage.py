"""Mailbox triage (830): sender and subject only, by plain rules. Receipts, bills, alerts, things to answer."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ORDER = ("alert", "bill", "answer", "receipt", "newsletter", "other")
_ALERT = re.compile(r"security alert|new sign-?in|new login|password (?:reset|changed)|verify your|suspicious|"
                    r"unusual activity|login attempt|otp\b", re.I)
_BILL = re.compile(r"\bbill\b|invoice|payment due|amount due|due (?:on|by|date)|\bemi\b|premium|statement|"
                   r"recharge|renewal|expires|overdue|fee notice|\bdue\b", re.I)
_RECEIPT = re.compile(r"receipt|order (?:confirm|has been|was|#)|your order|payment received|thank you for your "
                      r"(?:order|purchase)|delivered|shipped|booking confirm", re.I)
_ANSWER = re.compile(r"\?|^re:|please (?:reply|confirm|respond)|can you|could you|rsvp|action required|"
                     r"waiting for your", re.I)
_PROMO = re.compile(r"unsubscribe|newsletter|digest|\bweekly\b|promo|\boffer\b|\bsale\b|\d+% off|deal|discount|"
                    r"coupon", re.I)
LISTED = ("alert", "bill", "answer")  # these are named; promos and receipts are only counted
WORDS = {"alert": ("alert", "alerts"), "bill": ("bill", "bills"), "receipt": ("receipt", "receipts"),
         "answer": ("to answer", "to answer"), "newsletter": ("newsletter", "newsletters"), "other": ("other", "other")}


def classify(sender: str, subject: str, bulk: bool = False) -> str:
    subject, sender = str(subject or ""), str(sender or "")
    if _ALERT.search(subject):
        return "alert"
    if _BILL.search(subject) and not _PROMO.search(subject):
        return "bill"
    if _RECEIPT.search(subject):
        return "receipt"
    if bulk or _PROMO.search(subject) or _PROMO.search(sender):
        return "newsletter"
    if _ANSWER.search(subject):
        return "answer"
    return "other"


@dataclass
class Summary:
    total: int
    counts: dict[str, int]
    rows: dict[str, list[dict]] = field(default_factory=dict)


def triage(rows: list[dict]) -> Summary:
    counts = {k: 0 for k in ORDER}
    by: dict[str, list[dict]] = {k: [] for k in ORDER}
    for r in rows:
        kind = classify(r.get("who", ""), r.get("subject", ""), bool(r.get("bulk")))
        counts[kind] += 1
        by[kind].append(r)
    return Summary(len(rows), counts, by)


def render(s: Summary) -> str:
    if not s.total:
        return "No unread mail to triage."
    parts = [f"{n} {WORDS[k][n != 1]}" for k, n in s.counts.items() if n and k != "other"]
    head = f"Mailbox triage ({s.total} unread): " + (", ".join(parts) if parts else "nothing that needs sorting") + "."
    lines = [head]
    for kind in LISTED:
        for r in s.rows[kind][:4]:
            lines.append(f"{WORDS[kind][0].capitalize()}: {r.get('uid', '')}: {r.get('who', '')} — {r.get('subject', '')}")
    return "\n".join(lines)
