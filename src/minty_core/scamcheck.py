"""A scam and phishing check on text you paste (924). Heuristics only: it reads the text, names the links, and never
opens, fetches or contacts anything. It cannot promise a message is safe, and says so."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_I = re.IGNORECASE
SECRET_WORDS = r"(?:otp|pin|upi pin|mpin|cvv|password|passcode|card number|expiry|aadhaar|pan)"
ASK = re.compile(rf"\b(?:share|send|tell|give|provide|forward|read out|enter|reply with|type|submit)\b[^.?!]{{0,40}}"
                 rf"\b{SECRET_WORDS}\b", _I)
SHARE_IT = re.compile(r"\b(?:share|send|tell|give|read out) (?:it|the code|this code|the number)\b[^.?!]{0,30}"
                      r"\b(?:agent|executive|officer|team|caller|number)\b", _I)
NEGATED = re.compile(r"\b(?:do not|don'?t|dont|never|not to)\s+share\b|\bnever share\b|\bdo not disclose\b", _I)
VALID_FOR = re.compile(r"\bvalid for \d+ ?(?:minutes?|mins?)\b", _I)
ACCOUNT_THREAT = re.compile(r"\b(?:blocked|suspended|deactivated|disconnected|terminated|closed|expired|frozen|"
                            r"will be (?:blocked|closed|disconnected|terminated))\b", _I)
KYC = re.compile(r"\bkyc\b", _I)
URGENT = re.compile(r"\b(?:immediately|urgent(?:ly)?|within \d+ (?:hours?|mins?|minutes?)|today|tonight|right now|"
                    r"asap|hurry|last chance|expires? (?:today|soon)|in 24 hours)\b", _I)
PRIZE = re.compile(r"\b(?:won|winner|lottery|lucky draw|prize|congratulations|selected for|free iphone|gift voucher|"
                   r"cashback of)\b", _I)
FEE = re.compile(r"\b(?:processing|registration|clearance|shipping|release|handling|activation) (?:fee|charge)s?\b|"
                 r"\bpay rs\.? ?\d+ (?:to|for) (?:claim|release|receive)|\bsend rs\.? ?\d+", _I)
REMOTE = re.compile(r"\b(?:anydesk|teamviewer|quicksupport|rustdesk|airdroid|screen ?share|remote access)\b", _I)
JOB = re.compile(r"\b(?:part[- ]time job|work from home|earn rs\.? ?[\d,]+ (?:daily|per day|a day|per month|monthly)|"
                 r"like (?:youtube )?videos|no experience)\b", _I)
AUTHORITY = re.compile(r"\b(?:police|court|cbi|arrest|case has been filed|legal action|customs|warrant|narcotics)\b",
                       _I)
MONEY_WORDS = re.compile(r"\b(?:pay|fine|penalty|fee|transfer|send|clearance)\b", _I)
COLLECT = re.compile(r"\bcollect request\b|\brequest money\b|\bapprove the request\b|\baccidentally sent\b|"
                     r"\bsend it back\b|\bsent .{0,30} by mistake\b", _I)
APK = re.compile(r"\.apk\b|\binstall (?:this|the) (?:app|apk)\b", _I)
PRESS = re.compile(r"\bpress 1\b|\breply yes\b|\bcall (?:this|the) number\b", _I)
SENSITIVE_SEND = re.compile(r"\b(?:send|share|upload)\b[^.?!]{0,40}\b(?:aadhaar|pan|passport|selfie|photo of your card)\b",
                            _I)
URL = re.compile(r"(?:https?://)?((?:[a-z0-9\-]+\.)+[a-z]{2,})(?:/\S*)?", _I)
SHORTENERS = {"bit.ly", "tinyurl.com", "goo.gl", "cutt.ly", "rb.gy", "is.gd", "t.ly", "shorturl.at", "wa.me"}
SUSPICIOUS_TLDS = {"top", "xyz", "support", "click", "online", "site", "shop", "icu", "buzz", "live", "vip", "loan"}
BRANDS = ("hdfc", "sbi", "icici", "axis", "kotak", "paytm", "phonepe", "gpay", "amazon", "flipkart", "netflix", "jio",
          "airtel", "irctc", "kyc", "bank", "upi", "paypal", "google", "apple", "post", "gift")
KNOWN = {"amazon.in", "amazon.com", "flipkart.com", "spotify.com", "jio.com", "hdfcbank.com", "sbi.co.in",
         "icicibank.com", "axisbank.com", "irctc.co.in", "swiggy.com", "zomato.com", "netflix.com", "paytm.com",
         "phonepe.com", "google.com", "apple.com", "airtel.in", "bescom.in", "cult.fit", "wa.me"}
FILE_LIKE = {"apk", "pdf", "jpg", "png", "txt", "doc"}


@dataclass
class ScamCheck:
    verdict: str  # likely a scam | suspicious | no red flags found
    score: int
    reasons: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    text: str = ""


def _domains(text: str) -> list[str]:
    out = []
    for m in URL.finditer(text):
        d = m.group(1).lower()
        if d.split(".")[-1] in FILE_LIKE or re.fullmatch(r"\d+(\.\d+)+", d) or d.count(".") == 0:
            continue
        if d not in out:
            out.append(d)
    return out


def check(text: str) -> ScamCheck:
    t = " ".join((text or "").split())
    score, why = 0, []

    def hit(points: int, reason: str):
        nonlocal score
        score += points
        why.append(reason)

    ask = ASK.search(t) or SHARE_IT.search(t)
    if ask and not NEGATED.search(t):
        hit(3, "it asks you to share or enter a secret (OTP, PIN, CVV, card or ID details)")
        if re.search(r"\b(?:agent|executive|our (?:team|officer)|this number)\b", t, _I):
            hit(2, "and to hand it to a person or number it gives you")
    if ACCOUNT_THREAT.search(t) and re.search(r"\b(?:account|card|sim|service|connection|kyc|electricity|wallet|"
                                              r"payment)\b", t, _I):
        hit(2, "it threatens that something will be blocked, closed or cut off")
    if KYC.search(t):
        hit(1, "it mentions an urgent KYC update")
    if URGENT.search(t):
        hit(1, "it hurries you")
    if PRIZE.search(t):
        hit(3, "it says you have won something or been selected")
    if FEE.search(t):
        hit(3, "it asks for a fee or payment to release or claim something")
    if REMOTE.search(t):
        hit(4, "it asks you to install a remote-access app")
    if JOB.search(t):
        hit(3, "it offers easy money for little work")
    if AUTHORITY.search(t) and MONEY_WORDS.search(t):
        hit(3, "it uses police, customs or court threats with a payment")
    if COLLECT.search(t):
        hit(3, "it asks you to approve a payment request or send money back")
    if APK.search(t):
        hit(4, "it asks you to install an app file")
    if PRESS.search(t):
        hit(1, "it asks you to press, reply or call a number it gives")
    if SENSITIVE_SEND.search(t):
        hit(3, "it asks for ID documents or photos")
    links = _domains(t)
    for d in links:
        root = ".".join(d.split(".")[-2:]) if not d.endswith(("co.in", "org.in", "gov.in")) else ".".join(d.split(".")[-3:])
        if d in KNOWN or root in KNOWN:
            continue
        tld = d.split(".")[-1]
        if d in SHORTENERS:
            hit(2, f"the link {d} hides where it goes")
        elif tld in SUSPICIOUS_TLDS:
            hit(3, f"the link {d} is on an address ending .{tld}")
        elif any(b in d for b in BRANDS) and ("-" in d or any(c.isdigit() for c in d)):
            hit(3, f"the link {d} imitates a known name")
        elif d.count("-") >= 1:
            hit(1, f"the link {d} is an unusual address")
    if (NEGATED.search(t) or VALID_FOR.search(t)) and score and not ask:
        score = max(0, score - 2)
    verdict = "likely a scam" if score >= 4 else "suspicious" if score >= 2 else "no red flags found"
    if verdict == "no red flags found":
        msg = ("I found no red flags in it. That is not a guarantee: if it asks for money, a code or a login, stop "
               "and check with the person or company directly.")
    else:
        msg = (f"This looks {'like a scam' if score >= 4 else 'suspicious'}: " + "; ".join(why) + ". Don't click, "
               "reply or pay. A real bank or company never asks for your OTP, PIN or CVV, and doesn't mind "
               "waiting while you check by calling the number on your card or the official site. I haven't opened "
               "anything; this is a judgement from the text, not a guarantee.")
    if links:
        msg += " Links in it (not opened): " + ", ".join(links) + "."
    return ScamCheck(verdict, score, why, links, msg)
