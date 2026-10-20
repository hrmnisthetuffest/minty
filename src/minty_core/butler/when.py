"""Date phrases for butler items: "tomorrow", "in 3 days", "friday at 5 pm", "every 2 weeks".

Returns (due timestamp or None, repeat every N days or None). A date without a time is 09:00 local."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from minty_core.datewords import find_date

DEFAULT_HOUR = 9
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TIME = re.compile(r"\bat (\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b")
_EVERY = re.compile(r"\bevery (?:(\d+) )?(day|week|month)s?\b")
_EVERY_DAY = re.compile(r"\bevery (" + "|".join(_DAYS) + r")s?\b")
_IN = re.compile(r"\bin (\d+) (minute|hour|day|week)s?\b")
_WEEKDAY = re.compile(r"\b(?:on |next |this )?(" + "|".join(_DAYS) + r")\b")
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


def _at(day, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute)


def _next_weekday(now: datetime, name: str) -> datetime:
    ahead = (_DAYS.index(name) - now.weekday()) % 7 or 7  # today's name means next week's
    return now + timedelta(days=ahead)


def parse_when(text: str, now: datetime) -> tuple[float | None, int | None]:
    low = " ".join(str(text or "").lower().split())
    if not low:
        return None, None
    hour, minute = None, 0
    m = _TIME.search(low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        elif m.group(3) == "am" and hour == 12:
            hour = 0
        elif not m.group(3) and 1 <= hour <= 6:
            hour += 12  # "at 5" means the evening
        if hour > 23 or minute > 59:
            hour, minute = None, 0
    every: int | None = None
    base: datetime | None = None
    m = _EVERY_DAY.search(low)
    if m:
        every, base = 7, _next_weekday(now, m.group(1))
    else:
        m = _EVERY.search(low)
        if m:
            every = int(m.group(1) or 1) * _UNIT_DAYS[m.group(2)]
        elif re.search(r"\bdaily\b", low):
            every = 1
        elif re.search(r"\bweekly\b", low):
            every = 7
        elif re.search(r"\bmonthly\b", low):
            every = 30
        if every:
            base = now + timedelta(days=every)
    if base is None:
        m = _IN.search(low)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n), "day": timedelta(days=n),
                     "week": timedelta(weeks=n)}[unit]
            moment = now + delta
            if unit in ("minute", "hour"):
                return moment.timestamp(), every
            base = moment
        elif "tonight" in low:
            base, hour = now, 20 if hour is None else hour
        elif "tomorrow" in low:
            base = now + timedelta(days=1)
        elif re.search(r"\btoday\b", low):
            base = now
        else:
            m = _WEEKDAY.search(low)
            if m:
                base = _next_weekday(now, m.group(1))
            else:
                found = find_date(low, now.date())
                if found is not None:
                    base = datetime(found.year, found.month, found.day, now.hour, now.minute)
                elif hour is not None:
                    base = now
    if base is None:
        return None, every
    if hour is None and re.search(r"\bin \d+ days?\b", low) and not every:
        return base.replace(second=0, microsecond=0).timestamp(), every  # "in 3 days" keeps the time of day
    return _at(base, DEFAULT_HOUR if hour is None else hour, minute).timestamp(), every
