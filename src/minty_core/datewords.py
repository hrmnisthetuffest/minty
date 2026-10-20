"""Small helpers for dates and clock times in plain text: "20 Oct", "Oct 20 2026", "2026-10-20", "07:20"."""
from __future__ import annotations

import re
from datetime import date, time, timedelta

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


def parse_hhmm(text: str) -> time:
    h, m = str(text).split(":")
    return time(int(h), int(m))


_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.?"
                  r"(?:,?\s+(20\d\d))?\b", re.I)
_MDY = re.compile(r"\b(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                  r"(?:,?\s+(20\d\d))?\b", re.I)


def _date_in(text: str, today: date) -> date | None:
    m = _ISO.search(text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DMY.search(text)
    if m:
        return _build(int(m.group(1)), m.group(2), m.group(3), today)
    m = _MDY.search(text)
    if m:
        return _build(int(m.group(2)), m.group(1), m.group(3), today)
    return None


def find_date(text: str, today: date) -> date | None:
    """The first date in a piece of text (ISO, '20 Oct', 'Oct 20', with an optional year), else None."""
    return _date_in(text or "", today)


def _build(day: int, month: str, year: str | None, today: date) -> date | None:
    try:
        mon = [m[:3] for m in MONTHS].index(month.lower()[:3]) + 1
        y = int(year) if year else today.year
        d = date(y, mon, day)
        if not year and d < today - timedelta(days=180):
            d = date(y + 1, mon, day)  # a December notice about "10 Jan" means next January
        return d
    except ValueError:
        return None
