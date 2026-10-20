"""The contradiction detector (607): a new fact that disagrees with an old one becomes a question. Never an
overwrite. Plain patterns, no model: it only fires when the same thing (a time, a place, a number, a date) is given a
different value, so "likes tea" next to "likes mango lassi" is not a conflict."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text

EXPIRES_S = 30 * 86400
MAX_WORDS = 5
CHOICES = ("new", "old", "both")
_SUBJECT = re.compile(r"^(?:the )?(?:user|alex)(?:['’]s)?\s+|^my\s+|^the\s+|^i (?:am|'m)\s+", re.I)
_TIME = re.compile(r"^(?P<attr>.+?) (?:at|by) (?P<v>\d{1,2}[:.]\d{2}(?: ?[ap]m)?|\d{1,2} ?[ap]m)$")
_LIVES = re.compile(r"^(?:lives?|stays?|resides?|is based) in (?P<v>.+)$")
_WEIGHT = re.compile(r"^weighs? (?P<v>\d+(?:\.\d+)?) ?(?:kg|kilos?|kilograms?)$")
_IS = re.compile(r"^(?P<attr>[a-z][a-z' ]{1,40}?) (?:is|are|was) (?:on |at |in )?(?P<v>.+)$")
_ARTICLE = re.compile(r"^(?:a|an|the)\s+")


@dataclass(frozen=True)
class Claim:
    attr: str
    value: str


def _norm(text: str) -> str:
    t = " ".join(str(text).lower().replace("’", "'").split()).strip(" .!?;,")
    return t.replace("colour", "color").replace("favourite", "favorite")


def _attr(text: str) -> str:
    words = [re.sub(r"(?<=[a-z]{3})s$", "", w) for w in _ARTICLE.sub("", text).split()]
    return " ".join(words).strip()


def _value(text: str) -> str:
    v = _ARTICLE.sub("", _norm(text))
    return re.sub(r"(\d)\.(\d{2})", r"\1:\2", v)


def extract(text: str) -> list[Claim]:
    t = _SUBJECT.sub("", _norm(text))
    t = re.sub(r"^(?:i )?live (?=in\b)", "lives ", t)
    m = _LIVES.match(t)
    if m:
        return [Claim("lives in", _value(m.group("v")))]
    m = _WEIGHT.match(t)
    if m:
        return [Claim("weight", f"{m.group('v')} kg")]
    m = _TIME.match(t)
    if m:
        return [Claim(_attr(m.group("attr")), _value(m.group("v")))]
    m = _IS.match(t)
    if m:
        return [Claim(_attr(m.group("attr")), _value(m.group("v")))]
    return []


def _differs(a: str, b: str) -> bool:
    wa, wb = set(re.findall(r"[\w:]+", a)), set(re.findall(r"[\w:]+", b))
    if not wa or not wb or wa == wb or wa <= wb or wb <= wa:
        return False  # the same, or one only adds to the other ("blue" / "blue and green")
    return len(a.split()) <= MAX_WORDS and len(b.split()) <= MAX_WORDS


def conflicts(new: str, existing: list[tuple[str, str]]) -> list[tuple[str, str]]:
    claims = extract(new)
    if not claims:
        return []
    out = []
    for ref, old in existing:
        for oc in extract(old):
            if any(nc.attr == oc.attr and _differs(nc.value, oc.value) for nc in claims):
                out.append((ref, old))
                break
    return out


@dataclass
class Pending:
    id: int
    old_ref: str
    old_text: str
    new_text: str
    created: float
    status: str = "open"  # open | new | old | both


class ContradictionStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"next": 1, "items": []}
        except (OSError, ValueError):
            return {"next": 1, "items": []}

    def _save(self, data: dict) -> None:
        atomic_write_text(self.path, json.dumps(data), mode=0o600)

    def _all(self, data: dict) -> list[Pending]:
        return [Pending(**i) for i in data.get("items", [])]

    def open(self) -> list[Pending]:
        now = self._clock()
        return [p for p in self._all(self._load()) if p.status == "open" and p.created > now - EXPIRES_S]

    def raise_(self, old_ref: str, old_text: str, new_text: str) -> Pending:
        for p in self.open():
            if p.old_ref == old_ref and p.new_text == new_text:
                return p
        data = self._load()
        p = Pending(int(data.get("next", 1)), old_ref, old_text, new_text, self._clock())
        data["next"] = p.id + 1
        data.setdefault("items", []).append(p.__dict__)
        self._save(data)
        return p

    def resolve(self, ident: int, choice: str) -> Pending:
        if choice not in CHOICES:
            raise ValueError("Say whether to use the new one, keep the old one, or keep both.")
        data = self._load()
        for item in data.get("items", []):
            if item["id"] == ident:
                if item["status"] != "open":
                    raise ValueError("That question was already answered.")
                item["status"] = choice
                self._save(data)
                return Pending(**item)
        raise ValueError("There is no open question with that number.")


def prompt_line(store: ContradictionStore) -> str:
    """One line for the system prompt while a question is waiting, so it is asked in the next conversation."""
    open_ = store.open()
    if not open_:
        return ""
    p = open_[0]
    return (f"\n\n## Open question for the user\nI hold \"{p.old_text}\" but they also said \"{p.new_text}\". "
            f"Ask once, in a sentence, which is right. Then call memory_resolve with id {p.id} and choice new, old "
            "or both. Do not decide for them.")
