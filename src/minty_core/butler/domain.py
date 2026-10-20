"""A butler domain is data (`Domain`); `Butler` is the one service that gives every domain the same behaviour:
add, list, tick off, find, remind, export, delete, undo and a voice grammar."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from minty_core.butler.store import DAY, ButlerStore, Item
from minty_core.butler.when import parse_when
from minty_core.secretscan import mask_pii

_LONG_NUMBER = re.compile(r"(?<!\d)(?:\d[ \-]?){9,}(?!\d)")  # a document or account number, any kind
_PASSPORT = re.compile(r"\b[A-Z]\d{7}\b")
MAX_NAME = 80
MAX_VALUE = 120


class ButlerError(ValueError):
    """A refusal or a missing piece; the message is safe to say aloud."""


@dataclass(frozen=True)
class Domain:
    name: str
    label: str
    noun: str = "item"
    aliases: tuple[str, ...] = ()  # words that name it in speech: "chores", "rota"
    repeating: bool = False  # items may repeat ("every 3 days")
    needs_due: bool = False  # an item without a date is refused (follow-ups, renewals)
    remind: str = "{label}: {name}."  # what is said when one is due
    lead_days: tuple[int, ...] = ()  # also remind this many days before (renewals)
    fields: tuple[str, ...] = ()  # allowed keys in an item's data: "where", "who"
    patterns: tuple[tuple[str, str], ...] = ()  # (regex with named groups name/when/where/who, intent)
    renotify_h: int = 24
    default_when: str = ""  # used when no date was said (a follow-up in three days)
    unique_names: bool = False  # saying it again updates the item instead of adding a second one
    persistent: bool = False  # items are never ticked off for good (a checklist)
    seed: tuple[str, ...] = ()  # items a new list starts with

    def words(self) -> str:
        return "|".join(re.escape(w) for w in sorted({self.name, *self.aliases}, key=len, reverse=True))


@dataclass(frozen=True)
class Notice:
    domain: str
    item_id: int
    text: str
    due: float
    lead: int | None = None  # days before the due date, for an early reminder


def describe_due(ts: float | None, now: float) -> str:
    if ts is None:
        return ""
    when, today = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    days = (when.date() - today.date()).days
    word = ("today" if days == 0 else "tomorrow" if days == 1 else "yesterday" if days == -1
            else when.strftime("%A %d %b") if 0 < days < 7 else when.strftime("%d %b %Y"))
    if (when.hour, when.minute) != (9, 0):
        word += when.strftime(" at %-I:%M %p").replace(":00", "").lower()
    return word


class Butler:
    def __init__(self, store: ButlerStore, domains: dict[str, Domain], clock: Callable[[], float] = time.time):
        self.store, self.domains, self._clock = store, domains, clock

    def domain(self, name: str) -> Domain:
        d = self.domains.get(str(name or "").strip().lower())
        if d is None:
            raise ButlerError(f"I don't have a {name!r} list. I have: {', '.join(sorted(self.domains))}.")
        return d

    # ---- operations ----------------------------------------------------------------------------------
    @staticmethod
    def _clean(text: str, limit: int, what: str) -> str:
        text = " ".join(str(text or "").split())[:limit]
        if text and (mask_pii(text) != text or _LONG_NUMBER.search(text) or _PASSPORT.search(text)):
            raise ButlerError(f"That looks like an ID or card number. I don't keep those in {what}; "
                              "say only what it is and where it is.")
        return text

    def add(self, domain: str, name: str, when: str = "", data: dict | None = None, source: str = "user") -> Item:
        d = self.domain(domain)
        name = self._clean(name, MAX_NAME, d.label)
        if not name:
            raise ButlerError("What should I add?")
        clean = {}
        for key, value in (data or {}).items():
            if key in d.fields and str(value).strip():
                clean[key] = self._clean(value, MAX_VALUE, d.label)
        now = datetime.fromtimestamp(self._clock())
        due, every = parse_when(when, now)
        if due is None and d.default_when:
            due, _ = parse_when(d.default_when, now)
        if every and not d.repeating:
            every = None
        if d.needs_due and due is None:
            raise ButlerError(f"I need a date for that {d.noun}: say when, like 'friday' or '20 Oct'.")
        if d.unique_names:
            same = [i for i in self.store.list(d.name) if i.name.lower() == name.lower()]
            if same:
                return self.store.update(same[0].id, data={**same[0].data, **clean}, due=due)
        item = self.store.add(d.name, name, data=clean, due=due, every_days=every, source=source)
        self.store.log_undo(f"adding {name} to your {d.label}", "butler.delete", str(item.id))
        return item

    def _one(self, domain: str, ref: str) -> Item:
        found = self.store.find(self.domain(domain).name, ref)
        if not found:
            raise ButlerError(f"I can't find {ref!r} in your {self.domain(domain).label}.")
        if len(found) > 1:
            raise ButlerError("Which one: " + "; ".join(i.name for i in found[:4]) + "?")
        return found[0]

    def done(self, domain: str, ref: str) -> Item:
        return self.store.complete(self._one(domain, ref).id)

    def list(self, domain: str, status: str = "open") -> list[Item]:
        d = self.domain(domain)
        if d.seed and status == "open" and not self.store.get_meta(f"seeded:{d.name}"):
            self.store.set_meta(f"seeded:{d.name}", "1")
            if not self.store.list(d.name, "open", 1):
                for name in d.seed:
                    self.store.add(d.name, name, source="seed")
        return self.store.list(d.name, status)

    def find(self, domain: str, text: str) -> list[Item]:
        return self.store.find(self.domain(domain).name, text)

    def export(self, domain: str) -> str:
        return json.dumps(self.store.export(self.domain(domain).name), indent=2, ensure_ascii=False)

    def delete(self, domain: str, ref: str) -> int:
        d = self.domain(domain)
        if ref.strip().lower() == "all":
            return self.store.delete_domain(d.name)
        item = self._one(domain, ref)
        return int(self.store.delete(item.id))

    # ---- reminders -----------------------------------------------------------------------------------
    def notices(self, now: float) -> list[Notice]:
        out: list[Notice] = []
        for d in self.domains.values():
            for item in self.store.due(now, d.name, renotify_s=d.renotify_h * 3600):
                out.append(Notice(d.name, item.id, d.remind.format(label=d.label, name=item.name, noun=d.noun),
                                  item.due or now))
            if not d.lead_days:
                continue
            for item in self.store.upcoming(now, max(d.lead_days) * DAY, d.name):
                reached = [lead for lead in d.lead_days if (item.due or 0) - lead * DAY <= now]
                fresh = [lead for lead in reached if lead not in item.data.get("leads_sent", [])]
                if fresh:
                    days = max(1, round(((item.due or now) - now) / DAY))
                    out.append(Notice(d.name, item.id, f"{d.label}: {item.name} is due in {days} "
                                                       f"day{'s' * (days != 1)}.", item.due or now, min(fresh)))
        return out

    def mark(self, notice: Notice, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        if notice.lead is None:
            self.store.mark_notified(notice.item_id, now)
            return
        item = self.store.get(notice.item_id)
        if item is not None:
            d = self.domain(notice.domain)
            reached = [lead for lead in d.lead_days if (item.due or 0) - lead * DAY <= now]
            self.store.update(item.id, data={**item.data, "leads_sent": sorted(reached)})

    # ---- voice grammar ---------------------------------------------------------------------------------
    _DONE = re.compile(r"^(?:i )?(?:just )?(?:did|finished|completed|done with|have done|ticked off|tick off) "
                       r"(?:the |my )?(?P<name>.+)$", re.I)

    def _patterns(self, d: Domain) -> list[tuple[re.Pattern, str]]:
        words = d.words()
        base = [
            (rf"^(?:please )?(?:add|put|note|set) (?P<name>.+?) (?:to|on|in|onto) (?:my |the )?(?:{words})"
             rf"(?: list| rota| checklist)?(?: (?P<when>.+))?$", "add"),
            (rf"^(?:what(?:'s| is| are)|show|list|read)(?: me)?(?: what(?:'s| is)? on)? (?:my |the )?(?:{words})"
             rf"(?: list| rota| checklist)?(?: (?:today|due|left|now))?$", "list"),
        ]
        return [(re.compile(rx, re.I), intent) for rx, intent in (*base, *d.patterns)]

    def capture(self, text: str) -> str | None:
        """A reply when the phrase is butler business, else None (so the rest of Minty handles it)."""
        low = " ".join(str(text or "").strip().rstrip("?.!").split())
        if not low or len(low.split()) > 25:
            return None
        for d in self.domains.values():
            for rx, intent in self._patterns(d):
                m = rx.match(low)
                if m:
                    try:
                        return self._act(d, intent, m.groupdict())
                    except ButlerError as e:
                        return str(e)
        m = self._DONE.match(low)
        if m:
            hits = [i for d in self.domains.values() if not d.persistent
                    for i in self.store.find(d.name, m.group("name"))]
            if len(hits) == 1:
                done = self.store.complete(hits[0].id)
                return self._ticked(self.domains[hits[0].domain], done)
            if len(hits) > 1:
                return "Which one: " + "; ".join(i.name for i in hits[:4]) + "?"
        return None

    def _act(self, d: Domain, intent: str, g: dict) -> str | None:
        now = self._clock()
        if intent == "done":
            hits = [i for i in self.store.list(d.name) if (g.get("who") or "").lower() in i.name.lower()]
            if len(hits) != 1:
                return None
            return self._ticked(d, self.store.complete(hits[0].id))
        if intent == "add":
            data = {k: g[k] for k in ("where", "who") if g.get(k)}
            name = g.get("name") or ""
            if g.get("who") and name:
                name = f"{g['who'].strip()}: {name}"
            item = self.add(d.name, name, g.get("when") or "", data)
            tail = f" Due {describe_due(item.due, now)}." if item.due else ""
            if item.every_days:
                tail += f" It repeats every {item.every_days} day{'s' * (item.every_days != 1)}."
            where = f", {item.data['where']}" if item.data.get("where") else ""
            return f"Added {item.name}{where} to your {d.label}.{tail}"
        if intent == "list":
            items = self.list(d.name)
            if not items:
                return f"Your {d.label} is empty."
            if d.persistent:
                return "Before you go: " + ", ".join(i.name for i in items) + "."
            parts = [i.name + (f" ({describe_due(i.due, now)})" if i.due else "") for i in items[:8]]
            return f"Your {d.label}: " + ", ".join(parts) + (f", and {len(items) - 8} more." if len(items) > 8 else ".")
        if intent == "find":
            hits = self.find(d.name, g.get("name") or "")
            if not hits:
                return None
            i = hits[0]
            return f"{i.name}: {i.data.get('where') or 'I did not write down where'}."
        return None

    def _ticked(self, d: Domain, item: Item | None) -> str:
        if item is None:
            return "Done."
        if item.status == "open" and item.every_days:
            return (f"Ticked off {item.name}. Next one {describe_due(item.due, self._clock())}, "
                    "so I'll say it again then.")
        return f"Ticked off {item.name}."
