"""Background model work batched into one low-priority call per day (W0-26)."""
from datetime import datetime

from tests.test_history import Clock, noon
from minty_core.app import compaction_due, compact_once
from minty_core.context import build_context, parse_combined
from minty_core.history import ConversationArchive

DAY = 86400.0


async def test_one_call_per_finished_day_gives_both_summary_and_learned_rules(tmp_path):
    clock = Clock(noon(2026, 10, 10))
    archive = ConversationArchive(tmp_path, clock)
    archive.log("user", "I prefer short answers")
    archive.log("assistant", "Noted.")
    clock.now += DAY
    calls, learned = [], []

    async def combined(text):
        calls.append(text)
        return "Talked about answer length.", ["The user prefers short answers."]

    async def never(*a, **kw):
        raise AssertionError("separate summary call made")

    written = await archive.compact(never, combined=combined, on_learned=lambda day, lines: learned.extend(lines))
    assert written == ["days/2026-10-10"] and len(calls) == 1
    assert (tmp_path / "days" / "2026-10-10.md").read_text().strip() == "Talked about answer length."
    assert learned == ["The user prefers short answers."]


def test_parse_combined_reply():
    summary, rules = parse_combined("Did homework.\nPlanned the trip.\nLEARNED:\n- Likes cricket\n- Wants brief replies")
    assert summary == "Did homework.\nPlanned the trip." and rules == ["Likes cricket", "Wants brief replies"]
    assert parse_combined("Quiet day.\nLEARNED: NONE") == ("Quiet day.", [])
    assert parse_combined("No marker at all") == ("No marker at all", [])


def at(hour):
    return datetime(2026, 10, 12, hour, 0).timestamp()


def test_compaction_waits_for_the_night_and_enough_capacity(tmp_path):
    ctx = build_context(tmp_path)
    ctx.services["brownout"].capacity = lambda: type("C", (), {"share": 0.8})()
    assert not compaction_due(ctx, now=at(14))  # daytime: wait
    assert compaction_due(ctx, now=at(3))  # 03:00 with 80 % left
    ctx.services["brownout"].capacity = lambda: type("C", (), {"share": 0.3})()
    assert not compaction_due(ctx, now=at(3))  # not with less than half left
    ctx.services["brownout"].capacity = lambda: type("C", (), {"share": None})()
    assert compaction_due(ctx, now=at(3))  # unknown capacity does not block


def test_overdue_days_are_compacted_in_the_day_too(tmp_path):
    ctx = build_context(tmp_path)
    ctx.services["brownout"].capacity = lambda: type("C", (), {"share": 0.8})()
    archive = ctx.services["archive"]
    archive._clock = lambda: at(14) - 1 * DAY
    archive.log("user", "yesterday")
    assert not compaction_due(ctx, now=at(14))  # one day old: it can wait for the night
    archive._clock = lambda: at(14) - 3 * DAY
    archive.log("user", "three days ago")
    assert compaction_due(ctx, now=at(14))  # overdue (the computer may be off at night)


async def test_compact_once_skips_outside_the_window(tmp_path, monkeypatch):
    ctx = build_context(tmp_path)
    ran = []

    async def compact(*a, **kw):
        ran.append(kw)
        return []

    ctx.services["archive"].compact = compact
    monkeypatch.setattr("minty_core.app.compaction_due", lambda c, now=None: False)
    await compact_once(ctx)
    assert ran == []
    monkeypatch.setattr("minty_core.app.compaction_due", lambda c, now=None: True)
    await compact_once(ctx)
    assert ran and ran[0].get("combined") is not None
