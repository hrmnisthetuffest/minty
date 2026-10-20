"""685: ICS calendar feeds. Read-only, idempotent, recurrence-aware."""
import ast
import inspect
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from minty_core.integrations import ics
from minty_core.integrations.db import IntegrationsDB
from minty_core.integrations.ics import CalendarStore, expand, parse_ics

IST = ZoneInfo("Asia/Kolkata")

SAMPLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:maths@school
DTSTART;TZID=Asia/Kolkata:20261019T080000
DTEND;TZID=Asia/Kolkata:20261019T084500
RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR
EXDATE;TZID=Asia/Kolkata:20261021T080000
SUMMARY:Maths\\, Room 4
LOCATION:Block B
END:VEVENT
BEGIN:VEVENT
UID:maths@school
RECURRENCE-ID;TZID=Asia/Kolkata:20261023T080000
DTSTART;TZID=Asia/Kolkata:20261023T100000
DTEND;TZID=Asia/Kolkata:20261023T104500
SUMMARY:Maths (moved)
END:VEVENT
BEGIN:VEVENT
UID:holiday@x
DTSTART;VALUE=DATE:20261020
DTEND;VALUE=DATE:20261021
SUMMARY:Dussehra
END:VEVENT
BEGIN:VEVENT
UID:call@x
DTSTART:20261019T093000Z
DTEND:20261019T100000Z
SUMMARY:Call with a very long
  folded line here
DESCRIPTION:Line one\\nLine two
END:VEVENT
END:VCALENDAR
"""


def window(a, b):
    return datetime.fromisoformat(a).replace(tzinfo=IST), datetime.fromisoformat(b).replace(tzinfo=IST)


def occ(text, a, b):
    events = parse_ics(text)
    out = []
    lo, hi = window(a, b)
    for e in events:
        out += expand(e, events, lo, hi, IST)
    return sorted(out, key=lambda o: o.sort_key())


def test_events_parse_with_zones_escapes_folding_and_all_day():
    events = {e["uid"] + (e.get("rid") or ""): e for e in parse_ics(SAMPLE)}
    assert len(parse_ics(SAMPLE)) == 4
    m = events["maths@school"]
    assert m["summary"] == "Maths, Room 4" and m["location"] == "Block B" and m["rrule"]["FREQ"] == "WEEKLY"
    assert events["holiday@x"]["all_day"] is True and events["holiday@x"]["start"] == "2026-10-20"
    call = events["call@x"]
    assert call["summary"] == "Call with a very long folded line here" and call["description"] == "Line one\nLine two"
    assert datetime.fromisoformat(call["start"]).astimezone(IST).strftime("%H:%M") == "15:00"


def test_a_weekly_rule_honours_byday_exdate_and_a_moved_instance():
    got = occ(SAMPLE, "2026-10-19T00:00", "2026-11-01T00:00")
    maths = [(o.start.strftime("%a %d %H:%M")) for o in got if "Maths" in o.summary]
    assert maths == ["Mon 19 08:00", "Fri 23 10:00", "Mon 26 08:00", "Wed 28 08:00", "Fri 30 08:00"]
    assert [o.summary for o in got if o.all_day] == ["Dussehra"]
    assert [o.start.astimezone(IST).strftime("%H:%M") for o in got if o.summary.startswith("Call")] == ["15:00"]


@pytest.mark.parametrize("rule,expected", [
    ("FREQ=DAILY;COUNT=3", ["2026-10-19", "2026-10-20", "2026-10-21"]),
    ("FREQ=DAILY;INTERVAL=2;UNTIL=20261025T000000Z", ["2026-10-19", "2026-10-21", "2026-10-23"]),
    ("FREQ=MONTHLY;BYMONTHDAY=31;COUNT=3", ["2026-10-31", "2026-12-31", "2027-01-31"]),
    ("FREQ=YEARLY;COUNT=2", ["2026-10-19", "2027-10-19"]),
    ("FREQ=WEEKLY;INTERVAL=2;COUNT=3", ["2026-10-19", "2026-11-02", "2026-11-16"]),
])
def test_other_recurrence_rules(rule, expected):
    ics_text = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:r@x\nDTSTART;TZID=Asia/Kolkata:20261019T090000\nDTEND;TZID=Asia/Kolkata:20261019T100000\n"
                f"RRULE:{rule}\nSUMMARY:R\nEND:VEVENT\nEND:VCALENDAR\n")
    got = occ(ics_text, "2026-10-19T00:00", "2028-01-01T00:00")
    assert [o.start.strftime("%Y-%m-%d") for o in got] == expected  # (the monthly one skips months without a 31st)


def test_a_runaway_rule_is_capped():
    text = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:r@x\nDTSTART:20200101T000000Z\nRRULE:FREQ=DAILY\nSUMMARY:Forever\n"
            "END:VEVENT\nEND:VCALENDAR\n")
    events = parse_ics(text)
    lo, hi = window("2026-01-01T00:00", "2036-01-01T00:00")
    assert len(expand(events[0], events, lo, hi, IST)) <= 500


def test_garbage_and_huge_input_are_refused(tmp_path):
    assert parse_ics("not a calendar") == []
    store = CalendarStore(IntegrationsDB(tmp_path / "i.db"), IST)
    fid = store.add_feed("school", "https://calendar.example/private/abcd1234/basic.ics")
    with pytest.raises(ValueError, match="does not look like a calendar"):
        store.import_text(fid, "hello world")
    with pytest.raises(ValueError, match="too big"):
        store.import_text(fid, "BEGIN:VCALENDAR\n" + "X" * 3_000_000)


def make_store(tmp_path):
    return CalendarStore(IntegrationsDB(tmp_path / "i.db"), IST, clock=lambda: 1_760_000_000.0)


def test_import_is_idempotent_and_reports_only_real_changes(tmp_path):
    store = make_store(tmp_path)
    fid = store.add_feed("school", "https://calendar.example/private/abcd1234/basic.ics")
    first = store.import_text(fid, SAMPLE)
    assert (first.new, first.changed, first.removed) == (4, 0, 0)
    snapshot = store.dump()
    again = store.import_text(fid, SAMPLE)
    assert (again.new, again.changed, again.removed) == (0, 0, 0) and store.dump() == snapshot
    edited = store.import_text(fid, SAMPLE.replace("SUMMARY:Dussehra", "SUMMARY:Dussehra holiday"))
    assert (edited.new, edited.changed, edited.removed) == (0, 1, 0)
    gone = store.import_text(fid, re.sub(r"BEGIN:VEVENT\nUID:holiday@x.*?END:VEVENT\n", "", SAMPLE, flags=re.S))
    assert (gone.new, gone.changed, gone.removed) == (0, 0, 1)


def test_the_agenda_next_event_and_free_time_read_in_local_time(tmp_path):
    store = make_store(tmp_path)
    store.import_text(store.add_feed("school", "https://calendar.example/private/abcd1234/basic.ics"), SAMPLE)
    monday = store.agenda(date(2026, 10, 19))
    assert monday.startswith("Monday 19 Oct:") and "8:00 AM Maths, Room 4 (Block B)" in monday and "3:00 PM Call with a very long" in monday
    assert "Dussehra" in store.agenda(date(2026, 10, 20)) and "all day" in store.agenda(date(2026, 10, 20)).lower()
    assert store.agenda(date(2026, 10, 21)) == "Wednesday 21 Oct: nothing on the calendar."
    now = datetime(2026, 10, 19, 10, 0, tzinfo=IST)
    assert store.next_event(now).startswith("Next: Call with a very long folded line here at 3:00 PM today")
    slots = store.free_slots(date(2026, 10, 19), 60, now=datetime(2026, 10, 19, 7, 0, tzinfo=IST))
    assert slots == "Free on Monday 19 Oct (at least 60 minutes): 8:45 AM to 3:00 PM, 3:30 PM to 8:00 PM."


def test_the_feed_address_is_a_secret_and_only_https_is_accepted(tmp_path):
    store = make_store(tmp_path)
    fid = store.add_feed("school", "webcal://calendar.example/private/abcd1234/basic.ics")
    desc = store.feeds()[0]
    assert desc["id"] == fid and "abcd1234" not in str(desc) and desc["host"] == "calendar.example"
    assert store.url_for(fid).startswith("https://")
    for bad in ("http://calendar.example/x.ics", "file:///etc/passwd", "ftp://x/y.ics", "https://user:pw@calendar.example/x.ics",
                "https://127.0.0.1/x.ics", "https://localhost/x.ics"):
        with pytest.raises(ValueError):
            store.add_feed("bad", bad)


async def test_refresh_fetches_with_a_plain_get_and_never_writes_back(tmp_path):
    store = make_store(tmp_path)
    fid = store.add_feed("school", "https://calendar.example/private/abcd1234/basic.ics")
    seen = []

    async def fetch(url):
        seen.append(url)
        return SAMPLE

    res = await store.refresh(fid, fetch)
    assert res.new == 4 and seen == ["https://calendar.example/private/abcd1234/basic.ics"]
    tree = ast.parse(inspect.getsource(ics))
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
        (n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not called & {"post", "put", "patch", "request", "urlopen", "send"} and not imported & {"httpx", "requests", "urllib",
                                                                                                    "socket", "aiohttp"}
