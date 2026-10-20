"""971 urgency-aware interruptions and 951 the check-in timer."""
import json
from datetime import datetime, timedelta

import pytest

from minty_core.butler.checkin import CheckIn, CheckInError, template
from minty_core.butler.domain import Notice
from minty_core.butler.kit import ButlerProvider
from minty_core.butler.urgency import Digest, Interruptions, classify
from minty_core.config import Config

NOW = datetime(2026, 10, 15, 10, 0)


class Clock:
    def __init__(self, at=NOW):
        self.t = at.timestamp()

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw).total_seconds()


def notice(domain="chores", due=None, lead=None, text="Chore time: Plants."):
    return Notice(domain, 1, text, (due or NOW).timestamp(), lead)


# ---- 971 -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("n,expected", [
    (notice("documents", NOW, lead=30), "later"),
    (notice("documents", NOW, lead=7), "today"),
    (notice("documents", NOW, lead=1), "now"),
    (notice("documents", NOW), "now"),  # the renewal itself is due today
    (notice("chores", NOW.replace(hour=9, minute=0)), "today"),  # a date with no time: the daily digest
    (notice("tasks", NOW.replace(hour=10, minute=30)), "now"),  # you said a time and it is that time
    (notice("tasks", NOW.replace(hour=17, minute=0)), "today"),  # a time, but hours away
    (notice("checkin", NOW), "now"),
])
def test_classify(n, expected):
    assert classify(n, NOW.timestamp()) == expected


def test_only_a_few_interruptions_a_day_and_the_rest_wait(tmp_path):
    clock = Clock()
    ints = Interruptions(Digest(tmp_path / "d.json", clock), max_now=2, clock=clock)
    n = notice("tasks", NOW.replace(hour=10, minute=30))
    assert [ints.route(n) for _ in range(3)] == ["now", "now", "today"]
    clock.advance(days=1)
    assert ints.route(notice("tasks", NOW.replace(day=16, hour=10, minute=30))) == "now"  # a new day


def test_the_digest_keeps_each_line_once_and_flushes_it_as_one(tmp_path):
    clock = Clock()
    d = Digest(tmp_path / "d.json", clock)
    d.add("Chore time: Plants.", "today")
    d.add("Chore time: Plants.", "today")  # the same line twice
    d.add("Renewal: passport in 30 days.", "later")
    assert Digest(tmp_path / "d.json", clock).flush("today") == ["Chore time: Plants."]  # a restart keeps it
    assert d.flush("today") == []
    assert d.flush("later") == ["Renewal: passport in 30 days."]


def test_the_provider_sends_urgent_ones_at_once_and_batches_the_rest(tmp_path):
    clock = Clock()
    cfg = Config()
    cfg.butler.enabled = True
    cfg.butler.digest_times = ["08:30", "18:00"]
    p = ButlerProvider(config=lambda: cfg, directory=tmp_path, clock=clock)
    b = p.butler()
    b.add("tasks", "call the dentist", when="at 10:30")  # urgent at 10:30
    b.add("chores", "Plants", when="today")  # 09:00: due already, no time said
    urgent, normal = [], []
    clock.advance(minutes=45)
    p.deliver(normal.append, urgent.append)
    assert urgent == ["To do: call the dentist."] and normal == []
    clock.t = datetime(2026, 10, 16, 8, 31).timestamp()  # next morning, digest time passed
    p.deliver(normal.append, urgent.append)
    assert len(normal) == 1 and normal[0].startswith("Today: ") and "Plants" in normal[0]
    p.deliver(normal.append, urgent.append)
    assert len(normal) == 1  # said once


# ---- 951 -------------------------------------------------------------------------------------------------
@pytest.fixture
def checkin(tmp_path):
    return CheckIn(tmp_path / "checkin.json", clock=Clock())


def test_a_check_in_asks_at_the_time_then_waits_before_telling_the_contact(checkin):
    clock = checkin._clock
    checkin.start(90, label="a walk")
    assert checkin.tick() == []
    clock.advance(minutes=91)
    assert [a.kind for a in checkin.tick()] == ["ask"]
    assert checkin.tick() == []  # asked once
    clock.advance(minutes=11)
    (a,) = checkin.tick()
    assert a.kind == "contact" and "a walk" in a.text
    assert checkin.tick() == [] and checkin.state() is None  # told once, closed


def test_saying_you_are_fine_ends_it_at_any_point(checkin):
    clock = checkin._clock
    checkin.start(30)
    clock.advance(minutes=31)
    checkin.tick()
    assert checkin.confirm() is True
    clock.advance(hours=3)
    assert checkin.tick() == [] and checkin.confirm() is False


def test_it_survives_a_restart(tmp_path):
    clock = Clock()
    CheckIn(tmp_path / "c.json", clock=clock).start(60)
    again = CheckIn(tmp_path / "c.json", clock=clock)
    assert again.state() is not None
    clock.advance(minutes=61)
    assert [a.kind for a in again.tick()] == ["ask"]


def test_bounds_and_cancel(checkin):
    for bad in (0, 4, 24 * 60 + 1):
        with pytest.raises(CheckInError):
            checkin.start(bad)
    checkin.start(30)
    assert checkin.cancel() is True and checkin.state() is None and checkin.cancel() is False


def test_the_message_to_the_contact_is_fixed_text_with_no_location_or_data():
    text = template("Alex", datetime(2026, 10, 15, 12, 0), "a walk")
    assert "Alex" in text and "12:00" in text and "a walk" in text
    assert "http" not in text and "location" not in text.lower().replace("no location", "")
    assert len(text) < 300


def test_the_provider_runs_it_by_voice_and_only_when_opted_in(tmp_path):
    clock = Clock()
    cfg = Config()
    cfg.butler.enabled = True
    p = ButlerProvider(config=lambda: cfg, directory=tmp_path, clock=clock)
    assert p.capture("check in with me in 2 hours") is None  # checkin not enabled: not ours
    cfg.butler.checkin = True
    out = p.capture("check in with me in 2 hours")
    assert "2 hours" in out and "12:00" in out
    assert p.capture("I'm fine") == "Good. The check-in is closed."
    assert p.capture("I'm fine") is None  # nothing open: not ours
    p.capture("start a check-in for 45 minutes")
    assert p.capture("cancel the check-in") == "Check-in cancelled."


async def test_the_loop_step_tells_the_contact_once_and_says_so_locally(tmp_path):
    clock = Clock()
    cfg = Config()
    cfg.butler.enabled, cfg.butler.checkin = True, True
    cfg.butler.checkin_contact_chat, cfg.butler.checkin_contact_name = 4242, "Dad"
    p = ButlerProvider(config=lambda: cfg, directory=tmp_path, clock=clock)
    p.capture("check in with me in 30 minutes")
    sent, local = [], []

    async def send(chat_id, text):
        sent.append((chat_id, text))
        return True

    assert await p.check_in_step(send, local.append) == 0
    clock.advance(minutes=31)
    assert await p.check_in_step(send, local.append) == 1 and sent == [] and "okay" in local[0].lower()
    clock.advance(minutes=11)
    assert await p.check_in_step(send, local.append) == 1
    assert [c for c, _ in sent] == [4242] and "Dad" in local[-1]
    assert await p.check_in_step(send, local.append) == 0


async def test_one_loop_pass_sends_urgent_ones_flagged_urgent_and_runs_the_check_in(tmp_path):
    from types import SimpleNamespace

    from minty_core.app import _butler_tick

    clock = Clock()
    cfg = Config()
    cfg.butler.enabled, cfg.butler.checkin = True, True
    provider = ButlerProvider(config=lambda: cfg, directory=tmp_path, clock=clock)
    provider.butler().add("tasks", "call the dentist", when="at 10:30")
    provider.capture("check in with me in 30 minutes")
    events = []
    bus = SimpleNamespace(publish=lambda kind, **kw: events.append((kind, kw)))
    ctx = SimpleNamespace(services={"butler": provider}, bus=bus)
    clock.advance(minutes=45)
    await _butler_tick(ctx)
    assert ("notify", {"text": "To do: call the dentist.", "urgent": True}) in events
    assert any(k == "notify" and kw.get("urgent") and "okay" in kw["text"] for k, kw in events)  # the check-in ask


async def test_telegram_safe_closes_a_check_in(tmp_path):
    from types import SimpleNamespace

    from minty_core.telegram import commands as tg

    cfg = Config()
    cfg.butler.enabled, cfg.butler.checkin = True, True
    provider = ButlerProvider(config=lambda: cfg, directory=tmp_path, clock=Clock())
    provider.capture("check in with me in 30 minutes")
    gw = SimpleNamespace(handlers={}, machine="laptop")

    async def journal(name, args):
        return "ok"

    tg.install(gw, host_call=journal, safe=lambda: provider.capture("I'm fine"))
    assert await gw.handlers["/safe"](1, "") == "Good. The check-in is closed."
    assert await gw.handlers["/safe"](1, "") == "There is no check-in open."
