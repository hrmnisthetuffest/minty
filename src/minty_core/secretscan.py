"""Finding secrets and personal data in text and tool arguments, before anything leaves or is written down.

Secrets: values the key store holds, well-known key shapes, and a long random string right after a credential word.
Honeytokens: fake keys that look real; seeing one anywhere outgoing means something went looking for keys (an alarm).
Personal data: e-mail, Indian mobile numbers, payment cards (check digit verified), Aadhaar (checksum verified), PAN.
A bare long identifier (a Drive file id, a commit hash) is not a secret and is left alone.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

MIN_KNOWN = 8  # a stored value shorter than this is too short to match reliably

_PATTERNS: list[tuple[str, re.Pattern]] = [(label, re.compile(rx)) for label, rx in (
    ("openai-style key", r"\bsk-[A-Za-z0-9_\-]{20,}"),
    ("groq key", r"\bgsk_[A-Za-z0-9]{20,}"),
    ("google key", r"\bAIza[0-9A-Za-z_\-]{30,}"),
    ("github token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"),
    ("slack token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    ("aws key id", r"\bAKIA[0-9A-Z]{16}\b"),
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    ("bearer token", r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{24,}"),
)]
_CREDENTIAL_WORD = re.compile(
    r"(?i)(?:password|passwd|passphrase|secret|token|api[_ -]?key|credential)s?\W{0,8}(?:is|=|:)?\W{0,4}"
    r"([A-Za-z0-9_\-+/=]{24,})")


@dataclass(frozen=True)
class Finding:
    kind: str  # known | pattern | entropy | honeytoken | email | phone | card | aadhaar | pan
    label: str
    start: int
    end: int
    severity: str = "secret"  # secret | alarm | pii
    where: str = ""  # argument path, for scan_args


def _entropy(text: str) -> float:
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _mixed(text: str) -> bool:
    return sum(bool(re.search(rx, text)) for rx in (r"[a-z]", r"[A-Z]", r"\d")) >= 3


class SecretScanner:
    def __init__(self, known_values: Callable[[], Iterable[str]] = lambda: (),
                 honeytokens: Callable[[], Iterable[str]] = lambda: ()):
        self._known, self._honey = known_values, honeytokens

    def scan(self, text: str) -> list[Finding]:
        text = str(text or "")
        found: list[Finding] = []
        for token in self._honey():
            for m in re.finditer(re.escape(token), text):
                found.append(Finding("honeytoken", "a planted fake key", m.start(), m.end(), "alarm"))
        for value in self._known():
            if value and len(value) >= MIN_KNOWN:
                for m in re.finditer(re.escape(value), text):
                    found.append(Finding("known", "a stored key or password", m.start(), m.end()))
        for label, rx in _PATTERNS:
            for m in rx.finditer(text):
                found.append(Finding("pattern", label, m.start(), m.end()))
        for m in _CREDENTIAL_WORD.finditer(text):
            value = m.group(1)
            if _entropy(value) >= 3.3 and _mixed(value):
                found.append(Finding("entropy", "a random-looking secret", m.start(1), m.end(1)))
        return _dedupe(found)

    def scan_args(self, args, prefix: str = "") -> list[Finding]:
        """Walk nested arguments; each finding carries the path (body.lines[1])."""
        out: list[Finding] = []
        if isinstance(args, dict):
            for k, v in args.items():
                out += self.scan_args(v, f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(args, (list, tuple)):
            for i, v in enumerate(args):
                out += self.scan_args(v, f"{prefix}[{i}]")
        elif isinstance(args, str):
            out += [Finding(f.kind, f.label, f.start, f.end, f.severity, prefix) for f in self.scan(args)]
        return out

    def redact(self, text: str) -> str:
        text = str(text or "")
        for f in sorted(self.scan(text), key=lambda f: -f.start):
            text = text[:f.start] + "[secret]" + text[f.end:]
        return text


def _dedupe(found: list[Finding]) -> list[Finding]:
    """Overlapping findings collapse to the most serious, longest one."""
    rank = {"alarm": 0, "secret": 1, "pii": 2}
    out: list[Finding] = []
    for f in sorted(found, key=lambda f: (rank.get(f.severity, 3), -(f.end - f.start), f.start)):
        if not any(f.start < o.end and o.start < f.end for o in out):
            out.append(f)
    return sorted(out, key=lambda f: f.start)


# ---- honeytokens ------------------------------------------------------------------------------------------
_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class HoneyTokens:
    """Fake keys in the shape of real ones, made once and kept (mode 0600)."""

    def __init__(self, path: Path, rng: random.Random | None = None):
        self.path = Path(path)
        self._rng = rng or random.SystemRandom()
        self._tokens: list[str] | None = None

    def _make(self, prefix: str, n: int) -> str:
        return prefix + "".join(self._rng.choice(_ALNUM) for _ in range(n))

    def tokens(self) -> list[str]:
        if self._tokens is None:
            try:
                self._tokens = [str(t) for t in json.loads(self.path.read_text(encoding="utf-8"))["tokens"]]
            except (OSError, ValueError, KeyError, TypeError):
                self._tokens = [self._make("gsk_", 52), self._make("sk-", 48), self._make("AIza", 35)]
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"tokens": self._tokens}, f)
        return list(self._tokens)

    def plant_decoy(self, folder: Path) -> Path:
        """A file that looks like a key backup, where something hunting for keys would look. Nothing uses it."""
        decoy = Path(folder) / ".minty-decoy" / "api-keys.txt"
        if not decoy.exists():
            decoy.parent.mkdir(parents=True, exist_ok=True)
            names = ("GROQ_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")
            decoy.write_text("# backup of my API keys\n" + "".join(
                f"{n}={t}\n" for n, t in zip(names, self.tokens())), encoding="utf-8")
        return decoy


# ---- personal data ------------------------------------------------------------------------------------------
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
      [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
      [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
      [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_ok(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\d.])(?:\+91[\s\-]?|0)?[6-9]\d{4}[\s\-]?\d{5}(?![\d])")
_CARD = re.compile(r"(?<![\d.])(?:\d[ \-]?){13,19}(?![\d])")
_AADHAAR = re.compile(r"(?<![\d.])[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}(?![\d])")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def pii_findings(text: str) -> list[Finding]:
    text = str(text or "")
    found: list[Finding] = []
    for kind, rx in (("email", _EMAIL), ("pan", _PAN)):
        found += [Finding(kind, kind, m.start(), m.end(), "pii") for m in rx.finditer(text)]
    for m in _CARD.finditer(text):
        if luhn_ok(m.group(0)):
            found.append(Finding("card", "card", m.start(), m.end(), "pii"))
    for m in _AADHAAR.finditer(text):
        if verhoeff_ok(re.sub(r"\D", "", m.group(0))):
            found.append(Finding("aadhaar", "aadhaar", m.start(), m.end(), "pii"))
    found += [Finding("phone", "phone", m.start(), m.end(), "pii") for m in _PHONE.finditer(text)]
    priority = {"card": 0, "aadhaar": 1, "email": 2, "pan": 3, "phone": 4}
    out: list[Finding] = []
    for f in sorted(found, key=lambda f: (priority[f.kind], f.start)):
        if not any(f.start < o.end and o.start < f.end for o in out):
            out.append(f)
    return sorted(out, key=lambda f: f.start)


def mask_pii(text: str) -> str:
    text = str(text or "")
    for f in sorted(pii_findings(text), key=lambda f: -f.start):
        text = text[:f.start] + f"[{f.kind}]" + text[f.end:]
    return text
