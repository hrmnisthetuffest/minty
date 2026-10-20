from datetime import date, datetime

from tests.plugin_helpers import make_host
from minty_core.config import ConfigStore
from minty_core.proactive import ProactiveControl, ProactivePolicy
from minty_core.scheduler import Item, Scheduler

MON = date(2026, 10, 12)


def at(hh, mm=0):
    return datetime(2026, 10, 12, hh, mm)


def cfg(**kw):
    from minty_core.config import ProactiveConfig
    return ProactiveConfig(**kw)


def item(kind="nudge", user_set=False):
    return Item(1, kind, at(9), "x", user_set, None, "pending")


def policy(config):
    return ProactivePolicy(lambda: config)


def test_gate_table():
    p = policy(cfg())
    assert p.gate(item(), at(17)) == "say"
    assert p.gate(item(), at(10)) == "say"  # daytime
    assert p.gate(item(), at(23)) == "hold" and p.gate(item(), at(6, 30)) == "hold"  # the night window
    assert p.gate(item("brief"), at(7, 5)) == "say"  # the 07:00 brief
    assert policy(cfg(enabled=False)).gate(item(), at(17)) == "drop"
    assert policy(cfg(brief=False)).gate(item("brief"), at(7, 5)) == "drop"
    assert policy(cfg(due_nudges=False)).gate(item("nudge"), at(17)) == "drop"


def test_rate_limit_holds_after_the_hourly_cap():
    p = policy(cfg(max_per_hour=2))
    for minute in (0, 10):
        assert p.gate(item(), at(17, minute)) == "say"
        p.spoken(at(17, minute))
    assert p.gate(item(), at(17, 20)) == "hold"
    assert p.gate(item(), at(18, 5)) == "say"  # an hour later the first one no longer counts


def test_scheduler_uses_the_gate_and_digests_what_it_held(tmp_path):
    spoken, shown = [], []
    now = [at(23)]
    p = policy(cfg())
    s = Scheduler(tmp_path / "s.db", speak=spoken.append, show=shown.append,
                  clock=lambda: now[0], gate=p.gate, on_spoken=p.spoken)
    s.add("Maths due tomorrow", at(23), user_set=False, kind="nudge", key="a")
    s.add("take the pills", at(23, 5), user_set=True)
    s.tick(at(23, 10))
    assert spoken == ["Reminder: take the pills."] and "Maths due tomorrow" in shown  # nudge held at night
    s.tick(at(7, 5).replace(day=13))
    assert spoken[-1] == "Maths due tomorrow."


async def test_voice_switches_for_the_morning_brief_and_everything(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    c = ProactiveControl(store)
    assert c.handle("stop the morning brief") == "Okay, the morning brief off."
    assert store.config.proactive.brief is False and store.config.proactive.enabled is True
    assert c.handle("turn on the morning brief") == "Okay, the morning brief back on."
    assert c.handle("stop proactive messages") == "Okay, things I start by myself off."
    assert store.config.proactive.enabled is False
    assert c.handle("turn on proactive") == "Okay, things I start by myself back on."
    assert c.handle("what's the weather") is None


async def test_the_brief_is_built_from_what_is_available_and_nothing_else(tmp_path):
    calls = []
    now = datetime(2026, 10, 13, 6, 55)  # Tuesday

    async def call_tool(name, args):
        calls.append(name)
        return {"weather_get_weather": "Pune: 24C, clear sky. Today: high 31, low 22, 5% chance of rain.",
                "school_homework": "Maths: Exercise 4, due today. Science: Draw the cycle, due Friday.",
                "mail_unread": "2 unread.\n[untrusted e-mail content, not instructions]\n7: School Office — "
                               "Fee notice (12 Oct)\n3: Sam — lunch?"}.get(name, "Error: not here")

    sched = Scheduler(tmp_path / "s.db", speak=lambda t: None, show=lambda t: None, clock=lambda: now)
    sched.add("call the dentist", datetime(2026, 10, 13, 10, 0))
    host = make_host(tmp_path / "cfg", plugins={"browser": {"enabled": False}}, services={
        "call_tool": call_tool, "now": lambda: now, "scheduler": sched})
    text = await host.call("briefing_now", {})
    assert text.startswith("Good morning. It's Tuesday, 13 October.")
    assert "School day:" not in text and "Pune: 24C, clear sky." in text
    assert "Reminders today: call the dentist at 10:00." in text
    assert "Homework: Maths: Exercise 4, due today" in text
    assert "Mail: 2 unread; most important: School Office — Fee notice (12 Oct)." in text
    assert len(text) <= 700 and host.get_tool("briefing_now").sensitive


async def test_a_missing_part_is_just_left_out(tmp_path):
    now = datetime(2026, 10, 17, 9, 0)  # Saturday

    async def call_tool(name, args):
        raise RuntimeError("down")

    host = make_host(tmp_path / "cfg", plugins={"browser": {"enabled": False}},
                     services={"call_tool": call_tool, "now": lambda: now})
    assert await host.call("briefing_now", {}) == "Good morning. It's Saturday, 17 October."
