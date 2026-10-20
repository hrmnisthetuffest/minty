"""Calendar feeds from ICS addresses (685). Read-only by construction: the only network call is a plain GET of the feed (made by the
caller), there is no code that writes to a calendar, and re-importing the same feed changes nothing. Recurrence rules (daily, weekly
with days, monthly by day or nth weekday, yearly; INTERVAL, COUNT, UNTIL, EXDATE and moved instances) are expanded here because
`dateutil` is not installed. A feed address is a secret (it often grants access to your calendar), so it is never shown back."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from minty_core.integrations.db import IntegrationsDB
from minty_core.web.urls import check_web_url

MAX_CHARS = 2_000_000
MAX_OCCURRENCES, MAX_STEPS = 500, 20000
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _zone(name: str, default: tzinfo) -> tzinfo:
    if not name:
        return default
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(v: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _prop(line: str) -> tuple[str, dict, str] | None:
    if ":" not in line:
        return None
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    pdict = {}
    for p in params:
        k, _, v = p.partition("=")
        pdict[k.upper()] = v.strip('"')
    return name.upper(), pdict, value


def _when(value: str, params: dict, default: tzinfo) -> tuple[str, bool, str]:
    """(ISO string, all_day, zone name)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}", True, ""
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", value)
    if not m:
        raise ValueError(value)
    naive = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    if m.group(3):
        return naive.replace(tzinfo=timezone.utc).isoformat(), False, "UTC"
    zone = _zone(params.get("TZID", ""), default)
    return naive.replace(tzinfo=zone).isoformat(), False, params.get("TZID") or getattr(default, "key", "")


def _duration(value: str) -> timedelta:
    m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return timedelta(0)
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse_ics(text: str, default_tz: tzinfo = timezone.utc) -> list[dict]:
    events, cur = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"uid": "", "rid": "", "summary": "", "location": "", "description": "", "start": "", "end": "", "all_day": False,
                   "tz": "", "rrule": {}, "exdates": [], "status": "", "_dur": None}
        elif line == "END:VEVENT" and cur is not None:
            if cur["uid"] and cur["start"]:
                events.append(cur)
            cur = None
        elif cur is not None:
            p = _prop(line)
            if p is None:
                continue
            name, params, value = p
            try:
                if name == "UID":
                    cur["uid"] = value.strip()[:200]
                elif name == "SUMMARY":
                    cur["summary"] = _unescape(value).strip()[:200]
                elif name == "LOCATION":
                    cur["location"] = _unescape(value).strip()[:120]
                elif name == "DESCRIPTION":
                    cur["description"] = _unescape(value).strip()[:300]
                elif name == "STATUS":
                    cur["status"] = value.strip().upper()
                elif name == "DTSTART":
                    cur["start"], cur["all_day"], cur["tz"] = _when(value, params, default_tz)
                elif name == "DTEND":
                    cur["end"] = _when(value, params, default_tz)[0]
                elif name == "DURATION":
                    cur["_dur"] = _duration(value)
                elif name == "RRULE":
                    cur["rrule"] = dict(kv.split("=", 1) for kv in value.split(";") if "=" in kv)
                elif name == "EXDATE":
                    cur["exdates"] += [_when(v, params, default_tz)[0] for v in value.split(",") if v.strip()]
                elif name == "RECURRENCE-ID":
                    cur["rid"] = _when(value, params, default_tz)[0]
            except ValueError:
                continue
    for e in events:
        dur = e.pop("_dur")
        if not e["end"]:
            if e["all_day"]:
                e["end"] = (date.fromisoformat(e["start"]) + (dur or timedelta(days=1))).isoformat()
            else:
                e["end"] = (datetime.fromisoformat(e["start"]) + (dur or timedelta(0))).isoformat()
    return events


@dataclass
class Occurrence:
    uid: str
    summary: str
    start: datetime | date
    end: datetime | date
    all_day: bool
    location: str = ""
    description: str = ""

    def sort_key(self):
        s = self.start
        return (datetime(s.year, s.month, s.day, tzinfo=timezone.utc).timestamp() if self.all_day else s.timestamp(),
                0 if self.all_day else 1)


def _utc(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _nth_weekday(year: int, month: int, nth: int, wd: int) -> date | None:
    if nth > 0:
        first = date(year, month, 1)
        d = first + timedelta(days=(wd - first.weekday()) % 7 + 7 * (nth - 1))
    else:
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        last = nxt - timedelta(days=1)
        d = last - timedelta(days=(last.weekday() - wd) % 7 + 7 * (-nth - 1))
    return d if d.month == month else None


def _candidates(e: dict, tz: tzinfo):
    rule = e["rrule"]
    freq, interval = rule.get("FREQ", ""), max(1, int(rule.get("INTERVAL", "1") or 1))
    base = date.fromisoformat(e["start"]) if e["all_day"] else datetime.fromisoformat(e["start"]).astimezone(tz)
    when = lambda d: d if e["all_day"] else datetime.combine(d, base.timetz().replace(tzinfo=None), tzinfo=base.tzinfo)  # noqa: E731
    bd = rule.get("BYDAY", "").split(",") if rule.get("BYDAY") else []
    start_d = base if e["all_day"] else base.date()
    k = 0
    while True:
        if freq == "DAILY":
            yield when(start_d + timedelta(days=k * interval))
        elif freq == "WEEKLY":
            week = start_d - timedelta(days=start_d.weekday()) + timedelta(weeks=k * interval)
            wds = sorted(WEEKDAYS.index(x[-2:]) for x in bd if x[-2:] in WEEKDAYS) or [start_d.weekday()]
            for wd in wds:
                d = week + timedelta(days=wd)
                if d >= start_d:
                    yield when(d)
        elif freq == "MONTHLY":
            idx = start_d.month - 1 + k * interval
            y, m = start_d.year + idx // 12, idx % 12 + 1
            days = []
            if bd and any(re.fullmatch(r"-?\d?[A-Z]{2}", x) and x[:-2] for x in bd):
                for x in bd:
                    nth = int(x[:-2]) if x[:-2] else 1
                    d = _nth_weekday(y, m, nth, WEEKDAYS.index(x[-2:])) if x[-2:] in WEEKDAYS else None
                    if d:
                        days.append(d)
            else:
                for n in (int(x) for x in rule.get("BYMONTHDAY", str(start_d.day)).split(",")):
                    try:
                        days.append(date(y, m, n))
                    except ValueError:
                        pass
            for d in sorted(days):
                if d >= start_d:
                    yield when(d)
        elif freq == "YEARLY":
            try:
                d = date(start_d.year + k * interval, start_d.month, start_d.day)
            except ValueError:
                d = None
            if d:
                yield when(d)
        else:
            return
        k += 1


def expand(e: dict, all_events: list[dict], lo: datetime, hi: datetime, tz: tzinfo) -> list[Occurrence]:
    if e.get("status") == "CANCELLED":
        return []
    all_day = e["all_day"]
    span = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])) if all_day else (
        datetime.fromisoformat(e["end"]) - datetime.fromisoformat(e["start"]))

    def make(start) -> Occurrence:
        return Occurrence(e["uid"], e["summary"], start, start + span, all_day, e["location"], e["description"])

    def inside(o: Occurrence) -> bool:
        if all_day:
            s = datetime.combine(o.start, datetime.min.time(), tzinfo=lo.tzinfo)
            en = datetime.combine(o.end, datetime.min.time(), tzinfo=lo.tzinfo)
            return en > lo and s < hi
        return o.end > lo and o.start < hi or (o.start == o.end and lo <= o.start < hi)

    if e["rid"] or not e["rrule"]:
        start = date.fromisoformat(e["start"]) if all_day else datetime.fromisoformat(e["start"])
        o = make(start)
        return [o] if inside(o) else []
    ex = {_utc(datetime.fromisoformat(x)) if "T" in x else x for x in e["exdates"]}
    moved = {x["rid"] if x["all_day"] else _utc(datetime.fromisoformat(x["rid"])) for x in all_events
             if x["uid"] == e["uid"] and x["rid"]}
    count = int(e["rrule"].get("COUNT", "0") or 0)
    until = None
    if e["rrule"].get("UNTIL"):
        try:
            u = e["rrule"]["UNTIL"]
            until = datetime.strptime(u[:8], "%Y%m%d").replace(tzinfo=timezone.utc) + (
                timedelta(0) if "T" in u else timedelta(days=1)) if "T" not in u else datetime.strptime(
                u.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            until = None
    out, produced, steps = [], 0, 0
    for cand in _candidates(e, tz):
        steps += 1
        if steps > MAX_STEPS:
            break
        stamp = cand.isoformat() if all_day else cand
        if count and produced >= count:
            break
        if until is not None and (datetime.combine(cand, datetime.min.time(), tzinfo=timezone.utc) if all_day else cand) > until:
            break
        if (not all_day and cand >= hi + timedelta(days=1)) or (all_day and datetime.combine(
                cand, datetime.min.time(), tzinfo=lo.tzinfo) >= hi + timedelta(days=1)):
            break
        produced += 1
        key = cand.isoformat() if all_day else _utc(cand)
        if key in ex or key in moved:
            continue
        o = make(cand)
        if inside(o):
            out.append(o)
            if len(out) >= MAX_OCCURRENCES:
                break
    return out


@dataclass
class ImportResult:
    new: int = 0
    changed: int = 0
    removed: int = 0
    total: int = 0


class CalendarStore:
    def __init__(self, db: IntegrationsDB, tz: tzinfo, clock: Callable[[], float] = time.time):
        self.db, self.tz, self._clock = db, tz, clock

    # ---- feeds ----------------------------------------------------------------------------------------
    def add_feed(self, name: str, url: str) -> int:
        url = str(url).strip()
        if url.lower().startswith("webcal://"):
            url = "https://" + url[9:]
        if not url.lower().startswith("https://"):
            raise ValueError("A calendar address must be https (or webcal).")
        url = check_web_url(url)
        with self.db.lock:
            cur = self.db.db.execute("INSERT INTO cal_feeds(name, url) VALUES (?,?)", (" ".join(str(name).split())[:60] or "calendar", url))
            self.db.db.commit()
            return cur.lastrowid

    def url_for(self, feed_id: int) -> str:
        with self.db.lock:
            row = self.db.db.execute("SELECT url FROM cal_feeds WHERE id=?", (feed_id,)).fetchone()
        if row is None:
            raise ValueError("I have no such calendar.")
        return row["url"]

    def feeds(self) -> list[dict]:
        with self.db.lock:
            rows = self.db.db.execute("SELECT f.id, f.name, f.url, f.imported, (SELECT COUNT(*) FROM cal_events e WHERE e.feed_id=f.id) n "
                                      "FROM cal_feeds f ORDER BY f.id").fetchall()
        return [{"id": r["id"], "name": r["name"], "host": re.sub(r"^https://([^/]+).*$", r"\1", r["url"]), "events": r["n"],
                 "imported": r["imported"]} for r in rows]

    def remove_feed(self, feed_id: int) -> bool:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM cal_feeds WHERE id=?", (feed_id,)).rowcount
            self.db.db.execute("DELETE FROM cal_events WHERE feed_id=?", (feed_id,))
            self.db.db.commit()
        return bool(n)

    # ---- import (idempotent) --------------------------------------------------------------------------
    def import_text(self, feed_id: int, text: str) -> ImportResult:
        if len(text) > MAX_CHARS:
            raise ValueError("That calendar file is too big.")
        if "BEGIN:VCALENDAR" not in text:
            raise ValueError("That does not look like a calendar file.")
        self.url_for(feed_id)
        fresh = {f"{e['uid']}|{e['rid']}": e for e in parse_ics(text, self.tz)}
        res = ImportResult(total=len(fresh))
        with self.db.lock:
            have = {r["key"]: r["hash"] for r in self.db.db.execute("SELECT key, hash FROM cal_events WHERE feed_id=?", (feed_id,))}
            for key, e in fresh.items():
                data = json.dumps(e, sort_keys=True)
                digest = hashlib.sha1(data.encode()).hexdigest()
                if key not in have:
                    res.new += 1
                elif have[key] != digest:
                    res.changed += 1
                else:
                    continue
                self.db.db.execute("INSERT INTO cal_events(feed_id, key, data, hash) VALUES (?,?,?,?) "
                                   "ON CONFLICT(feed_id, key) DO UPDATE SET data=excluded.data, hash=excluded.hash",
                                   (feed_id, key, data, digest))
            for key in set(have) - set(fresh):
                res.removed += 1
                self.db.db.execute("DELETE FROM cal_events WHERE feed_id=? AND key=?", (feed_id, key))
            self.db.db.execute("UPDATE cal_feeds SET imported=? WHERE id=?", (self._clock(), feed_id))
            self.db.db.commit()
        return res

    async def refresh(self, feed_id: int, fetch: Callable[[str], Awaitable[str]]) -> ImportResult:
        return self.import_text(feed_id, await fetch(self.url_for(feed_id)))

    def dump(self) -> list[tuple]:
        with self.db.lock:
            return [tuple(r) for r in self.db.db.execute("SELECT feed_id, key, hash FROM cal_events ORDER BY feed_id, key")]

    # ---- reading --------------------------------------------------------------------------------------
    def _events(self) -> list[dict]:
        with self.db.lock:
            return [json.loads(r["data"]) for r in self.db.db.execute("SELECT data FROM cal_events ORDER BY feed_id, key")]

    def occurrences(self, lo: datetime, hi: datetime) -> list[Occurrence]:
        events = self._events()
        out = []
        for e in events:
            out += expand(e, events, lo, hi, self.tz)
        return sorted(out, key=lambda o: o.sort_key())

    def _day(self, day: date) -> list[Occurrence]:
        lo = datetime.combine(day, datetime.min.time(), tzinfo=self.tz)
        return self.occurrences(lo, lo + timedelta(days=1))

    def _local(self, o: Occurrence) -> datetime:
        return o.start.astimezone(self.tz)

    def agenda(self, day: date) -> str:
        head = f"{day:%A} {day.day} {day:%b}"
        items = []
        for o in self._day(day):
            where = f" ({o.location})" if o.location else ""
            items.append(f"{o.summary} (all day)" if o.all_day else f"{self._local(o):%H:%M} {o.summary}{where}")
        return f"{head}: " + "; ".join(items) + "." if items else f"{head}: nothing on the calendar."

    def next_event(self, now: datetime) -> str:
        now = now.astimezone(self.tz)
        for o in self.occurrences(now, now + timedelta(days=14)):
            if o.all_day:
                continue
            start = self._local(o)
            days = (start.date() - now.date()).days
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"on {start:%A} {start.day} {start:%b}"
            where = f" ({o.location})" if o.location else ""
            return f"Next: {o.summary} at {start:%H:%M} {when}{where}."
        return "Nothing is coming up in the next two weeks."

    def free_slots(self, day: date, min_minutes: int = 30, work: tuple[int, int] = (8, 20), now: datetime | None = None) -> str:
        lo = datetime.combine(day, datetime.min.time(), tzinfo=self.tz).replace(hour=work[0])
        hi = lo.replace(hour=work[1])
        if now is not None and now.astimezone(self.tz) > lo:
            lo = min(hi, now.astimezone(self.tz))
        busy = sorted((max(lo, self._local(o)), min(hi, o.end.astimezone(self.tz))) for o in self._day(day) if not o.all_day)
        gaps, cur = [], lo
        for s, e in busy:
            if s - cur >= timedelta(minutes=min_minutes):
                gaps.append((cur, s))
            cur = max(cur, e)
        if hi - cur >= timedelta(minutes=min_minutes):
            gaps.append((cur, hi))
        head = f"{day:%A} {day.day} {day:%b}"
        if not gaps:
            return f"No free stretch of {min_minutes} minutes on {head}."
        return f"Free on {head} (at least {min_minutes} minutes): " + ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in gaps) + "."
