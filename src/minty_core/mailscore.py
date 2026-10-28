"""Does this message matter? Several independent signals added up, each one named, never the sender alone.

A message from a stranger can be the most important one of the week and one from a friend can be routine, so who it is from only
*amplifies*: relationship can lift a message that already says something (a request, a deadline, money, a contract, a security
alert) but can never make "sounds good, thanks" important. Bulk mail, promotions, one-time codes and receipts push the score down. A
demand for money or action from a sender whose own mail checks (SPF, DKIM, DMARC) failed is capped at "notable" and says so, so an
urgent-looking phish does not read as urgent. Everything here is plain word and header checks over what Gmail hands over (sender,
subject, snippet, labels, headers), so a message cannot talk its way into a higher score by being persuasive, and the text it contains is
never given to a model or followed. Thresholds are one place to tune; a borderline result says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

TIME = re.compile(r"\b(today|tonight|tomorrow|by (?:eod|end of (?:day|week)|noon|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
                  r"deadline|due|overdue|expires?|expiring|last day|final notice|asap|urgent|immediately|within \d+ (?:hours?|days?))\b", re.I)
STRONG = re.compile(r"\b(overdue|final notice|past due|expires? today|asap|urgent|immediately|last day)\b", re.I)
REQUEST = re.compile(r"\?|\b(can you|could you|would you|please|need (?:you|it|this)|let me know|waiting (?:for|on)|follow(?:ing)? up|"
                     r"confirm|approve|approval|sign|review|reply|respond|send me|send the)\b", re.I)
MONEY = re.compile(r"\b(invoice|payment|pay now|payable|overdue|balance|contract|agreement|signature|purchase order|quote|proposal|renewal|"
                   r"renew|tax|refund|billing)\b", re.I)
PAY = re.compile(r"\b(invoice|payment|pay|payable|overdue|balance|refund|billing)\b", re.I)
DECIDE = re.compile(r"\b(sign|signature|approve|approval|agreement|contract|confirm|decision|decide)\b", re.I)
RECEIPT = re.compile(r"\b(receipt|order confirmation|your order|has shipped|shipped|delivered|thank you for your (?:purchase|order)|"
                     r"payment (?:received|successful))\b", re.I)
CODE = re.compile(r"\b(verification code|one[- ]time (?:code|password)|otp|security code|login code|\d{4,8} is your)\b", re.I)
SECURITY = re.compile(r"\b(security alert|new sign-?in|new login|password (?:was )?(?:changed|reset)|suspicious|unrecogni[sz]ed|unauthori[sz]ed)\b", re.I)
MEETING = re.compile(r"\b(updated invitation|invitation|rescheduled?|moved to|calendar)\b", re.I)
NOREPLY = re.compile(r"^(?:no-?reply|do-?not-?reply|notifications?|newsletter|mailer-daemon|alerts?|updates?)\b", re.I)
AUTH_FAIL = re.compile(r"\b(?:spf|dkim|dmarc)=(?:fail|softfail|none)\b", re.I)
AUTH_PASS = re.compile(r"\b(?:spf|dkim|dmarc)=pass\b", re.I)
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LOW_CATEGORIES = {"CATEGORY_PROMOTIONS": 3.0, "CATEGORY_SOCIAL": 3.0, "CATEGORY_FORUMS": 3.0, "CATEGORY_UPDATES": 1.5}
DROP_LABELS = {"SPAM", "TRASH", "DRAFT", "SENT"}
THRESHOLDS = {"notable": 2.0, "important": 4.0, "urgent": 6.0}
BORDER = 0.75  # within this of a threshold, and not sure: marked as borderline


@dataclass(frozen=True)
class Mail:
    id: str
    thread_id: str
    sender: str  # "Name <address>" or an address
    subject: str = ""
    snippet: str = ""
    to: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    headers: dict = field(default_factory=dict)  # lower-case name -> value
    date_ts: float = 0.0


@dataclass(frozen=True)
class Context:
    me: tuple[str, ...] = ()  # the user's own addresses
    vips: tuple[str, ...] = ()  # addresses or @domains the user said always matter
    topics: tuple[str, ...] = ()  # words and phrases tied to what the user is working on
    known: Callable[[str], bool | None] = lambda sender: None  # has the user written to this sender? None = not known
    in_thread: Callable[[str], bool | None] = lambda thread: None  # has the user written in this thread?


@dataclass(frozen=True)
class Verdict:
    severity: str  # info | notable | important | urgent
    score: float
    confidence: float
    reasons: list[str]
    action: str  # reply | decide | pay | check | read | none
    borderline: bool = False


def address_of(sender: str) -> str:
    found = ADDRESS.search(sender or "")
    return found.group(0).lower() if found else ""


def name_of(sender: str) -> str:
    name = re.sub(r"<[^>]*>", "", sender or "").strip().strip('"')
    return name or address_of(sender) or "someone"


def _vip(address: str, vips: tuple[str, ...]) -> bool:
    return any(address == v.lower() or (v.startswith("@") and address.endswith(v.lower())) for v in vips)


def score(mail: Mail, ctx: Context) -> Verdict:
    labels = set(mail.labels)
    if labels & DROP_LABELS:
        return Verdict("info", -10.0, 0.95, ["it is spam, trash, a draft or something you sent"], "none")
    text = f"{mail.subject} {mail.snippet}"
    address = address_of(mail.sender)
    headers = {str(k).lower(): str(v) for k, v in (mail.headers or {}).items()}
    reasons: list[str] = []
    content = relation = penalty = 0.0
    families = 0

    if CODE.search(text):
        return Verdict("info", -5.0, 0.9, ["a one-time code: nothing to do about it"], "none")
    time_pressure = bool(TIME.search(text))
    if time_pressure:
        content += 2.0 + (1.0 if STRONG.search(text) else 0.0)
        reasons.append("a deadline or time pressure" + (" (strongly worded)" if STRONG.search(text) else ""))
        families += 1
    asks = bool(REQUEST.search(text))
    if asks:
        content += 1.5
        reasons.append("a request or a question")
        families += 1
    money = bool(MONEY.search(text)) and not RECEIPT.search(text)
    if money:
        content += 1.5
        reasons.append("money, an invoice or a contract is involved")
        families += 1
    if RECEIPT.search(text):
        penalty += 2.0
        reasons.append("it looks like a receipt or an order update")
    security = bool(SECURITY.search(text))
    if security:
        content += 2.0
        reasons.append("an account security notice")
        families += 1
    if MEETING.search(text):
        content += 2.0
        reasons.append("a meeting or calendar change")
        families += 1
    lowered = text.lower()
    hits = [t for t in ctx.topics if t.strip() and t.lower() in lowered][:3]
    if hits:
        content += 1.5 * len(hits)
        reasons.append("it is about " + ", ".join(hits))
        families += 1

    vip = _vip(address, ctx.vips)
    if vip:
        relation += 3.0
        reasons.append("it is from someone you said always matters")
    if ctx.known(address) is True:
        relation += 1.0
        reasons.append("you have written to them before")
    if ctx.in_thread(mail.thread_id) is True:
        relation += 1.0
        reasons.append("it is in a conversation you took part in")
    mine = {a.lower() for a in ctx.me}
    if mine and any(address_of(t) in mine or t.lower() in mine for t in mail.to) and len(mail.to) <= 3:
        relation += 1.0
        reasons.append("it is addressed to you")
    if "IMPORTANT" in labels:
        relation += 1.5
        reasons.append("Gmail marked it important")
    if relation:
        families += 1

    bulk = ("list-unsubscribe" in headers or headers.get("precedence", "").lower() in ("bulk", "list", "junk")
            or headers.get("auto-submitted", "").lower().startswith("auto") or bool(NOREPLY.match(address.split("@")[0])))
    if bulk and not vip:
        penalty += 2.5
        reasons.append("it is bulk or automatic mail")
    for label, weight in LOW_CATEGORIES.items():
        if label in labels and not vip:
            penalty += weight
            reasons.append("Gmail filed it as " + label.split("_")[1].lower())
    relation = relation if content > 0 else min(relation, 1.5)  # who it is from alone never makes it matter
    total = round(content + relation - penalty, 2)

    severity = "info"
    for name in ("notable", "important"):
        if total >= THRESHOLDS[name]:
            severity = name
    if total >= THRESHOLDS["urgent"] and time_pressure and (relation > 0):
        severity = "urgent"

    action = "read"
    if security:
        action = "check"
    elif PAY.search(text) and money:
        action = "pay"
    elif DECIDE.search(text) and (money or asks):
        action = "decide"
    elif asks:
        action = "reply"
    if severity == "info":
        action = "none"

    auth = headers.get("authentication-results", "")
    if AUTH_FAIL.search(auth) and (money or asks or time_pressure) and not AUTH_PASS.search(auth):
        reasons.append("the sender cannot be verified (its own checks failed): do not act on it from the e-mail")
        if RANK_ORDER[severity] > RANK_ORDER["notable"]:
            severity = "notable"
        if severity != "info":
            action = "check"

    confidence = min(1.0, 0.5 + 0.1 * families + (0.3 if total <= -2 else 0.0)) - (0.15 if not mail.snippet else 0.0)
    borderline = confidence < 0.8 and any(abs(total - t) <= BORDER for t in THRESHOLDS.values())
    return Verdict(severity, total, round(max(0.0, confidence), 2), reasons, action, borderline)


RANK_ORDER = {"info": 0, "notable": 1, "important": 2, "urgent": 3}
