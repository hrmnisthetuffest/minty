"""The life tools, the daily check-in and the weekly review on the phone, and their schedule (WP-25)."""
from datetime import date, datetime, timedelta, timezone

from tests.plugin_helpers import make_host
from tests.test_telegram_gateway import OWNER, STRANGER, build
from minty_core.events import EventBus
from minty_core.finance import Ledger
from minty_core.health import HealthHub
from minty_core.life import due_events
from minty_core.telegram.life import LifeDelivery
from minty_core.telegram.push import PushRouter

DAY = 86400.0
NOW_DT = datetime(2026, 10, 18, 20, 5)  # a Sunday, after 20:00
NOW = NOW_DT.timestamp()


def hub(tmp_path):
    h = HealthHub(tmp_path / "h.db", clock=lambda: NOW)
    for d in ("training", "sleep", "body", "nutrition", "activity"):
        h.grant(d)
    h.set_profile("sessions_per_week", "3")
    return h


async def test_life_tools(tmp_path):
    h = hub(tmp_path)
    led = Ledger(tmp_path / "f.db", clock=lambda: NOW)
    plugin = make_host(tmp_path / "cfg", services={"health": h, "finance": led})
    h.add_workout("hevy", "w", NOW - DAY, "Legs", 3600,
                  [{"exercise": "Squat (Barbell)", "idx": 0, "weight_kg": 100, "reps": 5}])
    assert "1 session" in await plugin.call("life_view", {"view": "training"})
    assert "nothing" in (await plugin.call("life_briefing", {})).lower() or "Training" in \
        await plugin.call("life_briefing", {})
    review = await plugin.call("life_review", {})
    assert "Misses" in review or "Not enough" in review or "Wins" in review
    assert "Saved" in await plugin.call("life_goal", {"action": "add", "objective": "Stronger",
                                                      "key_result": "Squat max", "target": "150", "unit": "kg",
                                                      "due": "2027-01-18", "source": "e1rm:Squat (Barbell)"})
    listing = await plugin.call("life_goal", {"action": "list"})
    assert "Squat max" in listing and "%" in listing
    assert "Noted" in await plugin.call("life_checkin", {"energy": "high", "priority_done": True})
    assert (await plugin.call("life_view", {"view": "everything"})).startswith("Error")
    for name in ("life_view", "life_briefing", "life_review", "life_goal", "life_checkin"):
        assert plugin.get_tool(name).sensitive is True


def setup(tmp_path, quiet=False, private=False, checkins=True):
    gw, fake, store, _ = build(tmp_path, private=private)
    data = store.config.model_dump(mode="json")
    data["life"].update(checkins=checkins, review_push=True)
    store.update(data)
    h = hub(tmp_path)
    push = PushRouter(gw, EventBus(), quiet=lambda now: quiet, state_path=tmp_path / "held.json",
                      clock=lambda: NOW_DT)
    delivery = LifeDelivery(gw, push, lambda: h)
    delivery.install()
    return gw, fake, h, push, delivery


async def test_the_check_in_is_two_questions_with_buttons_and_the_answers_are_stored(tmp_path):
    gw, fake, h, push, delivery = setup(tmp_path)
    assert await delivery.handle({"type": "life", "kind": "checkin", "day": "2026-10-18"}) == "sent"
    rows = fake.sent[0]["reply_markup"]["inline_keyboard"]
    assert [len(r) for r in rows] == [3, 2] and all(len(b["callback_data"].encode()) <= 64 for r in rows for b in r)
    await gw.handle_update(fake.press(OWNER, rows[0][2]["callback_data"]))  # energy: high
    await gw.handle_update(fake.press(OWNER, rows[1][0]["callback_data"]))  # priority: yes
    row = h.checkins("2026-10-18", "2026-10-18")[0]
    assert row["energy"] == "high" and row["priority_done"] == 1
    await gw.handle_update(fake.press(STRANGER, rows[0][0]["callback_data"]))
    assert h.checkins("2026-10-18", "2026-10-18")[0]["energy"] == "high"  # a stranger changed nothing
    await gw.handle_update(fake.press(OWNER, "ck:2026-10-18:z:q"))
    assert fake.answers[-1]["text"] == "That one is out of date."


async def test_a_review_is_pushed_as_a_quiet_message_and_held_in_quiet_hours(tmp_path):
    gw, fake, h, push, delivery = setup(tmp_path)
    assert await delivery.handle({"type": "life", "kind": "review", "text": "Wins: x. One suggestion: y."}) == "sent"
    assert fake.sent[0]["disable_notification"] is True and fake.sent[0].get("reply_markup") is None
    gw2, fake2, _, push2, d2 = setup(tmp_path / "q", quiet=True)
    assert await d2.handle({"type": "life", "kind": "review", "text": "Wins: x."}) == "held"
    assert fake2.sent == [] and len(push2._held) == 1


async def test_nothing_is_sent_on_a_private_day_or_with_the_check_in_off(tmp_path):
    gw, fake, h, push, delivery = setup(tmp_path, private=True)
    assert await delivery.handle({"type": "life", "kind": "checkin", "day": "2026-10-18"}) == "private"
    gw2, fake2, h2, p2, d2 = setup(tmp_path / "off", checkins=False)
    assert await d2.handle({"type": "life", "kind": "checkin", "day": "2026-10-18"}) == "off"
    assert fake.sent == fake2.sent == []


def test_due_events_come_once_a_day_and_once_a_week_at_the_set_times(tmp_path):
    h = hub(tmp_path)
    led = Ledger(tmp_path / "f.db", clock=lambda: NOW)
    cfg = {"checkins": True, "checkin_time": "20:00", "review_push": True, "review_time": "18:00"}
    h.add_workout("hevy", "w", NOW - DAY, "Legs", 3600, [])
    first = due_events(h, led, cfg, NOW_DT)
    kinds = [e["kind"] for e in first]
    assert kinds == ["checkin", "review"]
    assert due_events(h, led, cfg, NOW_DT) == []  # the same minute again: nothing
    assert due_events(h, led, cfg, NOW_DT.replace(hour=7)) == []
    nxt = due_events(h, led, cfg, NOW_DT + timedelta(days=1))  # Monday evening: a check-in, no review
    assert [e["kind"] for e in nxt] == ["checkin"]
    assert due_events(h, led, {**cfg, "checkins": False, "review_push": False}, NOW_DT + timedelta(days=2)) == []
