"""The clock and the calendar: the time, the date, days until, other time zones, leap years."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

from minty_core.instant.base import Query, Rule
from minty_core.instant.text import num, parse_duration, plural, say_duration

CATEGORY = "time and date"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
          "december"]
_MONTH_ALIASES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
                  "nov": 11, "dec": 12, **{m: i + 1 for i, m in enumerate(MONTHS)}}
_MONTH = "(" + "|".join(sorted(_MONTH_ALIASES, key=len, reverse=True)) + ")"
_DAY = "(" + "|".join(WEEKDAYS) + ")"
HOLIDAYS = {  # fixed-date days only (month, day); the ones that move (Diwali, Easter) are left to the model
    "christmas": ("Christmas", 12, 25), "christmas day": ("Christmas", 12, 25), "christmas eve": ("Christmas Eve", 12, 24),
    "new years day": ("New Year's Day", 1, 1), "new year": ("New Year's Day", 1, 1), "new years": ("New Year's Day", 1, 1),
    "new years eve": ("New Year's Eve", 12, 31), "valentines day": ("Valentine's Day", 2, 14),
    "valentines": ("Valentine's Day", 2, 14), "halloween": ("Halloween", 10, 31), "republic day": ("Republic Day", 1, 26),
    "gandhi jayanti": ("Gandhi Jayanti", 10, 2), "pi day": ("Pi Day", 3, 14), "april fools day": ("April Fools' Day", 4, 1),
    "april fools": ("April Fools' Day", 4, 1), "earth day": ("Earth Day", 4, 22),
    "womens day": ("International Women's Day", 3, 8), "international womens day": ("International Women's Day", 3, 8),
}
_HOLIDAY = "(" + "|".join(sorted(HOLIDAYS, key=len, reverse=True)) + ")"
ZONES = {  # name people say -> tz database name
    **{c: z for c, z in {
        "new york": "America/New_York", "boston": "America/New_York", "miami": "America/New_York", "atlanta": "America/New_York",
        "washington": "America/New_York", "toronto": "America/Toronto", "montreal": "America/Toronto",
        "chicago": "America/Chicago", "houston": "America/Chicago", "dallas": "America/Chicago",
        "mexico city": "America/Mexico_City", "denver": "America/Denver", "phoenix": "America/Phoenix",
        "los angeles": "America/Los_Angeles", "san francisco": "America/Los_Angeles", "seattle": "America/Los_Angeles",
        "las vegas": "America/Los_Angeles", "vancouver": "America/Vancouver", "anchorage": "America/Anchorage",
        "honolulu": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu", "sao paulo": "America/Sao_Paulo",
        "buenos aires": "America/Argentina/Buenos_Aires", "bogota": "America/Bogota", "lima": "America/Lima",
        "santiago": "America/Santiago", "london": "Europe/London", "uk": "Europe/London", "england": "Europe/London",
        "dublin": "Europe/Dublin", "lisbon": "Europe/Lisbon", "paris": "Europe/Paris", "france": "Europe/Paris",
        "berlin": "Europe/Berlin", "germany": "Europe/Berlin", "madrid": "Europe/Madrid", "spain": "Europe/Madrid",
        "rome": "Europe/Rome", "italy": "Europe/Rome", "amsterdam": "Europe/Amsterdam", "brussels": "Europe/Brussels",
        "vienna": "Europe/Vienna", "zurich": "Europe/Zurich", "stockholm": "Europe/Stockholm", "oslo": "Europe/Oslo",
        "copenhagen": "Europe/Copenhagen", "helsinki": "Europe/Helsinki", "athens": "Europe/Athens",
        "warsaw": "Europe/Warsaw", "prague": "Europe/Prague", "budapest": "Europe/Budapest", "kyiv": "Europe/Kyiv",
        "kiev": "Europe/Kyiv", "moscow": "Europe/Moscow", "russia": "Europe/Moscow", "istanbul": "Europe/Istanbul",
        "turkey": "Europe/Istanbul", "cairo": "Africa/Cairo", "egypt": "Africa/Cairo", "lagos": "Africa/Lagos",
        "nigeria": "Africa/Lagos", "nairobi": "Africa/Nairobi", "kenya": "Africa/Nairobi",
        "johannesburg": "Africa/Johannesburg", "cape town": "Africa/Johannesburg", "south africa": "Africa/Johannesburg",
        "casablanca": "Africa/Casablanca", "accra": "Africa/Accra", "addis ababa": "Africa/Addis_Ababa",
        "dubai": "Asia/Dubai", "abu dhabi": "Asia/Dubai", "uae": "Asia/Dubai", "doha": "Asia/Qatar",
        "riyadh": "Asia/Riyadh", "saudi arabia": "Asia/Riyadh", "tehran": "Asia/Tehran", "baghdad": "Asia/Baghdad",
        "jerusalem": "Asia/Jerusalem", "tel aviv": "Asia/Jerusalem", "karachi": "Asia/Karachi", "lahore": "Asia/Karachi",
        "islamabad": "Asia/Karachi", "pakistan": "Asia/Karachi", "india": "Asia/Kolkata", "delhi": "Asia/Kolkata",
        "new delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "kolkata": "Asia/Kolkata", "chennai": "Asia/Kolkata",
        "bangalore": "Asia/Kolkata", "hyderabad": "Asia/Kolkata", "chandigarh": "Asia/Kolkata",
        "kathmandu": "Asia/Kathmandu", "nepal": "Asia/Kathmandu", "colombo": "Asia/Colombo", "sri lanka": "Asia/Colombo",
        "dhaka": "Asia/Dhaka", "bangladesh": "Asia/Dhaka", "bangkok": "Asia/Bangkok", "thailand": "Asia/Bangkok",
        "hanoi": "Asia/Ho_Chi_Minh", "vietnam": "Asia/Ho_Chi_Minh", "jakarta": "Asia/Jakarta",
        "indonesia": "Asia/Jakarta", "singapore": "Asia/Singapore", "kuala lumpur": "Asia/Kuala_Lumpur",
        "malaysia": "Asia/Kuala_Lumpur", "manila": "Asia/Manila", "philippines": "Asia/Manila",
        "hong kong": "Asia/Hong_Kong", "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "china": "Asia/Shanghai",
        "taipei": "Asia/Taipei", "seoul": "Asia/Seoul", "south korea": "Asia/Seoul", "tokyo": "Asia/Tokyo",
        "japan": "Asia/Tokyo", "perth": "Australia/Perth", "adelaide": "Australia/Adelaide",
        "brisbane": "Australia/Brisbane", "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
        "canberra": "Australia/Sydney", "auckland": "Pacific/Auckland", "wellington": "Pacific/Auckland",
        "new zealand": "Pacific/Auckland",
    }.items()},
    "utc": "UTC", "gmt": "UTC", "zulu": "UTC", "ist": "Asia/Kolkata", "est": "America/New_York", "edt": "America/New_York",
    "cst": "America/Chicago", "cdt": "America/Chicago", "mst": "America/Denver", "mdt": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles", "cet": "Europe/Paris", "cest": "Europe/Paris",
    "bst": "Europe/London", "jst": "Asia/Tokyo", "aest": "Australia/Sydney",
}
_ZONE_NAME = {"utc": "UTC", "gmt": "UTC", "zulu": "UTC", "uk": "the UK", "uae": "the UAE", "usa": "the USA"}


def spoken_time(dt: datetime) -> str:
    if dt.minute == 0 and dt.hour in (0, 12):
        return "midnight" if dt.hour == 0 else "noon"
    h12, suffix = dt.hour % 12 or 12, "AM" if dt.hour < 12 else "PM"
    return f"{h12} {suffix}" if dt.minute == 0 else f"{h12}:{dt.minute:02d} {suffix}"


def say_date(d: date, today: date | None = None, weekday: bool = True) -> str:
    text = f"{d.day} {MONTHS[d.month - 1].title()}"
    if weekday:
        text = f"{WEEKDAYS[d.weekday()].title()}, {text}"
    if today is None or d.year != today.year:
        text += f" {d.year}"
    return text


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _next_date(month: int, day: int, today: date) -> date | None:
    for year in (today.year, today.year + 1, today.year + 2, today.year + 4):
        try:
            d = date(year, month, day)
        except ValueError:
            continue  # 29 February
        if d >= today:
            return d
    return None


def _parse_date(s: str, today: date) -> tuple[date, bool] | None:
    """"15 august", "august 15th", "the 15th of august 2027" -> (date, year was said); the next one when no year is said."""
    s = " ".join(s.split())
    m = re.fullmatch(rf"(?:the )?(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? {_MONTH}(?: (\d{{4}}))?", s) or None
    if m:
        day, month, year = int(m[1]), _MONTH_ALIASES[m[2]], m[3]
    else:
        m = re.fullmatch(rf"{_MONTH} (?:the )?(\d{{1,2}})(?:st|nd|rd|th)?(?: (\d{{4}}))?", s)
        if not m:
            return None
        month, day, year = _MONTH_ALIASES[m[1]], int(m[2]), m[3]
    try:
        if year:
            return date(int(year), month, day), True
    except ValueError:
        return None
    d = _next_date(month, day, today)
    return (d, False) if d else None


def _target(s: str, today: date) -> tuple[date, str] | None:
    """A day someone may count towards: a holiday, a weekday, "the weekend" or a date -> (date, how to call it)."""
    plain = s.replace("'", "")
    if plain in HOLIDAYS:
        label, month, day = HOLIDAYS[plain]
        d = _next_date(month, day, today)
        return (d, label) if d else None
    m = re.fullmatch(rf"(?:next |this |coming |upcoming )?{_DAY}", s)
    if m:
        want = WEEKDAYS.index(m[1])
        ahead = (want - today.weekday()) % 7
        if ahead == 0 and not s.startswith(("this", "coming")) and s.split()[0] != m[1]:
            ahead = 7
        return today + timedelta(days=ahead), m[1].title()
    if s in ("the weekend", "weekend", "saturday and sunday"):
        return today + timedelta(days=(5 - today.weekday()) % 7), "the weekend"
    parsed = _parse_date(s, today)
    if parsed:
        d, had_year = parsed
        return d, say_date(d, today, weekday=False) if had_year else f"{d.day} {MONTHS[d.month - 1].title()}"
    return None


# ---------------------------------------------------------------------------------------------- now
_TIME = re.compile(r"^(?:what )?(?:is )?(?:the )?(?:current |local )?time(?: is it)?(?: now| today)?$|^what time is it(?: now| here)?$|"
                   r"^(?:do you have|got|have you got) the time$|^what time do we have$|^what is the time right now$")
_DATE = re.compile(r"^(?:what )?(?:is )?(?:the |today's |todays )?(?:current )?(?:date|day and date|date today)(?: is it| today)?$|"
                   r"^what date is it(?: today)?$|^what is the date today$|^todays date$")
_DAYNAME = re.compile(r"^what day (?:is it|is today|of the week is it|of the week is today)(?: today)?$|^which day is (?:it|today)$|"
                      r"^what is today$|^what day is it today$|^today$")
_MONTH_Q = re.compile(r"^(?:what|which) month is it(?: now)?$|^what is the (?:current )?month$|^current month$")
_YEAR_Q = re.compile(r"^(?:what|which) year is it(?: now)?$|^what is the (?:current )?year$|^current year$")
_WEEK_Q = re.compile(r"^(?:what|which) week (?:of the year )?is it$|^what is the week number$|^week number$|^what week are we in$")
_WEEKEND_Q = re.compile(r"^is (?:it|today) (?:the |a )?(weekend|weekday|week day)(?: today| yet)?$")


def time_rule(q: Query) -> str | None:
    return f"It's {spoken_time(q.env.now())}." if _TIME.match(q.cmd) else None


def date_rule(q: Query) -> str | None:
    return f"It's {say_date(q.env.now().date())}." if _DATE.match(q.cmd) else None


def day_rule(q: Query) -> str | None:
    now = q.env.now()
    if _DAYNAME.match(q.cmd):
        return f"It's {WEEKDAYS[now.weekday()].title()}, {now.day} {MONTHS[now.month - 1].title()}."
    return None


def month_year_rule(q: Query) -> str | None:
    now = q.env.now()
    if _MONTH_Q.match(q.cmd):
        return f"It's {MONTHS[now.month - 1].title()}."
    if _YEAR_Q.match(q.cmd):
        return f"It's {now.year}."
    if _WEEK_Q.match(q.cmd):
        year, week, _ = now.isocalendar()
        return f"It's week {week} of {year}."
    return None


def weekend_rule(q: Query) -> str | None:
    m = _WEEKEND_Q.match(q.cmd)
    if not m:
        return None
    now = q.env.now()
    day = WEEKDAYS[now.weekday()].title()
    return f"Yes, it's {day}." if (now.weekday() >= 5) == (m[1] == "weekend") else f"No, it's {day}."


# ---------------------------------------------------------------------------------------------- other days
_REL_WORD = r"(tomorrow|yesterday|the day after tomorrow|the day before yesterday)"
_REL = [re.compile(p) for p in (
    rf"^what (?:day|date)(?: of the week)? (?:is|was|will it be)(?: it)? {_REL_WORD}$",
    rf"^what (?:is|was) {_REL_WORD}$",
    rf"^what (?:is|was) the (?:date|day) {_REL_WORD}$",
    rf"^(?:what is )?{_REL_WORD}(?:'s)? (?:date|day)$",
)]
_REL_OFFSET = {"tomorrow": 1, "yesterday": -1, "the day after tomorrow": 2, "the day before yesterday": -2}
_IN_N = re.compile(r"^what (?:day|date) (?:is|will it be)(?: it)? in (\d+) (day|week|month)s?$|"
                   r"^(?:what is )?the (?:day|date) in (\d+) (day|week|month)s?$|"
                   r"^what (?:day|date) (?:is|will it be)(?: it)? (\d+) (day|week)s? from (?:now|today)$|"
                   r"^(?:what is )?the (?:day|date) (\d+) (day|week)s? from (?:now|today)$")
_AGO = re.compile(r"^what (?:day|date) (?:was|is)(?: it)? (\d+) (day|week|month)s? ago$|^(?:what is )?the (?:day|date) (\d+) "
                  r"(day|week|month)s? ago$")
_WHEN_DAY = re.compile(rf"^(?:when is|what (?:day|date) is|what is the date (?:of|on)|what date is|what day is) "
                       rf"(next |this |last |coming |upcoming )?{_DAY}$")
_WHEN_HOLIDAY = re.compile(rf"^(?:when is|what (?:day|date) is|what day of the week is|what day is|what date is) {_HOLIDAY}"
                           rf"(?: this year| next year| coming up)?$")


def _add_months(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(d.day, _days_in_month(year, month + 1)))


def other_days(q: Query) -> str | None:
    now = q.env.now()
    today = now.date()
    cmd = q.digits
    for pattern in _REL:
        m = pattern.match(cmd)
        if m:
            offset = _REL_OFFSET[m[1]]
            d = today + timedelta(days=offset)
            lead = {1: "Tomorrow is", -1: "Yesterday was", 2: "The day after tomorrow is", -2: "The day before yesterday was"}[offset]
            return f"{lead} {say_date(d, today)}."
    m = _IN_N.match(cmd)
    if m:
        g = [x for x in m.groups() if x]
        n, unit = int(g[0]), g[1]
        d = _add_months(today, n) if unit == "month" else today + timedelta(days=n * (7 if unit == "week" else 1))
        return f"In {plural(n, unit)} it will be {say_date(d, today)}."
    m = _AGO.match(cmd)
    if m:
        g = [x for x in m.groups() if x]
        n, unit = int(g[0]), g[1]
        d = _add_months(today, -n) if unit == "month" else today - timedelta(days=n * (7 if unit == "week" else 1))
        return f"{plural(n, unit).capitalize()} ago it was {say_date(d, today)}."
    m = _WHEN_DAY.match(cmd)
    if m:
        how, want = (m[1] or "").strip(), WEEKDAYS.index(m[2])
        if how == "last":
            d = today - timedelta(days=(today.weekday() - want) % 7 or 7)
            return f"Last {m[2].title()} was {say_date(d, today)}."
        ahead = (want - today.weekday()) % 7
        if ahead == 0 and how in ("next", ""):
            ahead = 0 if how == "" else 7
        d = today + timedelta(days=ahead)
        if ahead == 0:
            return f"That's today, {say_date(d, today)}."
        return f"{'Next ' if how == 'next' else ''}{m[2].title()} is {say_date(d, today, weekday=False)}."
    m = _WHEN_HOLIDAY.match(cmd.replace("'", ""))
    if m:
        label, month, day = HOLIDAYS[m[1]]
        year = today.year + (1 if "next year" in cmd else 0)
        d = date(year, month, day)
        if year == today.year and d < today and "this year" not in cmd:
            d = date(year + 1, month, day)
        verb = "was" if d < today else "is"
        return f"{label} {verb} on {say_date(d, today)}."
    return None


# ---------------------------------------------------------------------------------------------- counting days
_UNTIL = re.compile(r"^(?:how many days|how long|how many sleeps|how many weeks|days)(?: are there| is it| do i have| are left| left)?"
                    r"(?: until| till| to| before| from now until| from now till) (?:the )?(.+)$")
_LEFT = re.compile(r"^how many days (?:are )?(?:left|remaining)(?: (?:in|of|this))?(?: the| this)?(?: (year|month))?$|"
                   r"^how many days until the end of (?:the |this )?(year|month)$")
_LEFT_YEAR = re.compile(r"^how many days (?:are )?(?:left|remaining) in (\d{4})$")
_SINCE = re.compile(r"^how many days (?:has it been |are there |is it )?since (?:the )?(.+)$")


def days_until(q: Query) -> str | None:
    today = q.env.now().date()
    cmd = q.digits
    m = _LEFT.match(cmd) or _LEFT_YEAR.match(cmd)
    if m:
        unit = "year"
        if m.re is _LEFT:
            unit = m[1] or m[2] or "year"
        elif int(m[1]) != today.year:
            return None
        end = date(today.year, 12, 31) if unit == "year" else date(today.year, today.month, _days_in_month(today.year, today.month))
        where = str(today.year) if unit == "year" else MONTHS[today.month - 1].title()
        return f"{plural((end - today).days, 'day')} left in {where}."
    m = _UNTIL.match(cmd)
    if m:
        found = _target(m[1], today)
        if found is None:
            return None
        d, label = found
        n = (d - today).days
        if label == "the weekend" and today.weekday() >= 5:
            return "It's the weekend now."
        if n == 0:
            return f"{label} is today."
        if n == 1:
            return f"{label} is tomorrow."
        if cmd.startswith("how many weeks"):
            return f"{label} is in {num(round(n / 7, 1))} weeks, which is {plural(n, 'day')}."
        return f"{plural(n, 'day')} until {label}."
    m = _SINCE.match(cmd)
    if m:
        parsed = _parse_date(m[1], today)
        if parsed is None or not parsed[1]:
            return None  # without a year it is a guess
        n = (today - parsed[0]).days
        if n < 0:
            return None
        return f"{plural(n, 'day')} since {say_date(parsed[0], None, weekday=False)}."
    return None


# ---------------------------------------------------------------------------------------------- clock arithmetic
_AHEAD = re.compile(r"^what time (?:will it be|is it going to be|would it be) in (.+)$")
_BEHIND = re.compile(r"^what time (?:was it|will it have been) (.+) ago$")
_HOWLONG = re.compile(r"^how (?:long|much time|many hours|many minutes)(?: is it| is there| do i have| are there| left)?"
                      r"(?: until| till| to| before) (\d{1,2})(?::(\d{2}))? ?(am|pm)?$|"
                      r"^how (?:long|much time|many hours|many minutes)(?: is it| is there| do i have| are there| left)?"
                      r"(?: until| till| to| before) (noon|midnight)$")


def clock_math(q: Query) -> str | None:
    now = q.env.now()
    cmd = q.digits
    m = _AHEAD.match(cmd)
    if m:
        seconds = parse_duration(m[1])
        if seconds:
            return f"In {say_duration(seconds)} it will be {spoken_time(now + timedelta(seconds=seconds))}."
    m = _BEHIND.match(cmd)
    if m:
        seconds = parse_duration(m[1])
        if seconds:
            return f"{say_duration(seconds).capitalize()} ago it was {spoken_time(now - timedelta(seconds=seconds))}."
    m = _HOWLONG.match(cmd)
    if m:
        if m[4]:
            hour, minute, options = (12 if m[4] == "noon" else 0), 0, None
        else:
            hour, minute, meridiem = int(m[1]), int(m[2] or 0), m[3]
            if minute > 59 or hour > 24:
                return None
            options = None
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            elif meridiem is None and hour <= 12:
                options = (hour % 12, hour % 12 + 12)  # no am or pm said: whichever comes next
        best = None
        for h in (options or (hour % 24,)):
            target = now.replace(hour=h, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            if best is None or target < best:
                best = target
        gap = (best - now).total_seconds()
        gap = round(gap / 60) * 60 if gap >= 120 else gap  # "27 minutes", not "27 minutes 55 seconds"
        return f"{say_duration(gap)} until {spoken_time(best)}."
    return None


# ---------------------------------------------------------------------------------------------- other places
_WORLD = [re.compile(p) for p in (
    r"^(?:what )?(?:is )?(?:the )?(?:current )?time (?:is it )?(?:right now )?in (?:the )?(.+)$",
    r"^what time is it (?:right now )?in (?:the )?(.+)$",
    r"^(.+) time(?: now)?$",
)]
_MYZONE = re.compile(r"^(?:what is|which) (?:is )?(?:my )?time ?zone(?: am i in)?$|^what time zone am i in$|^which time zone am i in$|"
                     r"^my time ?zone$")


def world_time(q: Query) -> str | None:
    now = q.env.now()
    if _MYZONE.match(q.cmd):
        zone = now.astimezone()
        offset = zone.utcoffset()
        total = int(offset.total_seconds() // 60) if offset else 0
        sign = "+" if total >= 0 else "-"
        return f"Your time zone is {zone.tzname()} (UTC{sign}{abs(total) // 60:02d}:{abs(total) % 60:02d})."
    place = None
    for pattern in _WORLD:
        m = pattern.match(q.cmd)
        if m and m[1].strip() in ZONES:
            place = m[1].strip()
            break
    if place is None:
        return None
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            zone = ZoneInfo(ZONES[place])
        except ZoneInfoNotFoundError:
            return None
    except ImportError:
        return None
    here = now.astimezone() if now.tzinfo is None else now
    there = here.astimezone(zone)
    name = _ZONE_NAME.get(place, place.title())
    day = ""
    if there.date() > here.date():
        day = " tomorrow"
    elif there.date() < here.date():
        day = " yesterday"
    return f"It's {spoken_time(there)}{day} in {name}."


# ---------------------------------------------------------------------------------------------- the calendar
_LEAP_YEAR = re.compile(r"^is (\d{4}) a leap year$|^was (\d{4}) a leap year$")
_LEAP_NOW = re.compile(r"^is (?:this|it) (?:a )?leap year$|^is this year a leap year$|^is it a leap year(?: this year)?$")
_DAYS_IN_MONTH = re.compile(rf"^how many days (?:are )?(?:there )?in (?:the month of )?{_MONTH}(?: (\d{{4}}))?$")
_DAYS_IN_REL = re.compile(r"^how many days (?:are )?(?:there )?in (this|next|last) month$")
_DAYS_IN_YEAR = re.compile(r"^how many days (?:are )?(?:there )?in (?:a|the|one|this) year$|^days in a year$")


def calendar_facts(q: Query) -> str | None:
    now = q.env.now()
    m = _LEAP_YEAR.match(q.cmd)
    if m:
        year = int(m[1] or m[2])
        return f"Yes, {year} is a leap year." if calendar.isleap(year) else f"No, {year} is not a leap year."
    if _LEAP_NOW.match(q.cmd):
        return (f"Yes, {now.year} is a leap year." if calendar.isleap(now.year) else f"No, {now.year} is not a leap year.")
    m = _DAYS_IN_MONTH.match(q.cmd)
    if m:
        month = _MONTH_ALIASES[m[1]]
        if m[2]:
            year = int(m[2])
        elif month == 2:
            return "February has 28 days, or 29 in a leap year."
        else:
            year = now.year
        return f"{MONTHS[month - 1].title()} {year} has {_days_in_month(year, month)} days."
    m = _DAYS_IN_REL.match(q.cmd)
    if m:
        d = _add_months(now.date().replace(day=1), {"this": 0, "next": 1, "last": -1}[m[1]])
        return f"{MONTHS[d.month - 1].title()} {d.year} has {_days_in_month(d.year, d.month)} days."
    if _DAYS_IN_YEAR.match(q.cmd):
        return "A year has 365 days, or 366 in a leap year."
    return None


RULES = (
    Rule("time now", CATEGORY, ("what time is it", "what's the time", "tell me the time", "current time", "time please",
                                "got the time", "what's the time right now", "do you have the time", "what time is it now"),
         time_rule),
    Rule("date now", CATEGORY, ("what's the date", "what's today's date", "what date is it", "today's date", "what is the date today"),
         date_rule),
    Rule("day now", CATEGORY, ("what day is it", "what day is today", "which day is it", "what's today", "what day of the week is it"),
         day_rule),
    Rule("month and year", CATEGORY, ("what month is it", "what year is it", "what's the year", "which month is it",
                                      "current month", "what week is it", "what's the week number"), month_year_rule),
    Rule("weekend", CATEGORY, ("is it the weekend", "is it a weekday", "is today the weekend"), weekend_rule),
    Rule("other days", CATEGORY, ("what day is tomorrow", "what's the date tomorrow", "what was yesterday",
                                  "what day was yesterday", "what's tomorrow's date", "what day is it in 10 days",
                                  "what date is it in two weeks", "what day was it 3 days ago", "when is next friday",
                                  "what date is monday", "when is christmas", "what day is christmas",
                                  "what day of the week is halloween", "what's yesterday's date"), other_days),
    Rule("days until", CATEGORY, ("how many days until christmas", "how many days till new year", "days until halloween",
                                  "how many days until the weekend", "how many days until friday",
                                  "how many days until 15 august", "how many days left in the year",
                                  "how many days are left this month", "how many days since 1 january 2020",
                                  "how many days until december 31", "how many weeks until christmas"), days_until),
    Rule("clock maths", CATEGORY, ("what time will it be in 2 hours", "what time will it be in 45 minutes",
                                   "what time was it 3 hours ago", "how long until 6 pm", "how many hours until 5",
                                   "how many minutes until noon", "how long till midnight",
                                   "what time will it be in an hour and a half"), clock_math),
    Rule("world time", CATEGORY, ("what time is it in tokyo", "time in london", "what's the time in new york",
                                  "what time is it in india", "dubai time", "what time is it in utc", "time in sydney",
                                  "what's the time in los angeles right now", "what is my time zone"), world_time),
    Rule("calendar", CATEGORY, ("is 2028 a leap year", "is this a leap year", "how many days in february",
                                "how many days are in march", "how many days in a year", "how many days are in this month",
                                "how many days in february 2028"), calendar_facts),
)
