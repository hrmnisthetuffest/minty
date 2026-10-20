"""WP-25 acceptance: an honest weekly review, and quiet hours for every push this package adds."""
import re
from datetime import date, datetime, timedelta, timezone

from tests.plugin_helpers import make_host
from tests.test_telegram_gateway import build
from minty_core.events import EventBus
from minty_core.finance import Ledger
from minty_core.health import HealthHub
from minty_core.life import weekly_review
from minty_core.telegram.coach import CoachDelivery
from minty_core.telegram.life import LifeDelivery
from minty_core.telegram.push import PushRouter

DAY = 86400.0
NOW = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc).timestamp()
TODAY = date(2026, 10, 18)


def seed(tmp_path):
    h = HealthHub(tmp_path / "h.db", clock=lambda: NOW)
    for d in ("training", "sleep", "body", "nutrition", "activity"):
        h.grant(d)
    h.set_profile("sessions_per_week", "4")
    h.add_body("health_connect", "w", NOW - DAY, "weight", 55.0, "kg")
    for i, (d, w) in enumerate(((13, 80), (9, 82.5), (5, 85), (1, 87.5))):
        h.add_workout("hevy", f"b{i}", NOW - d * DAY, "Push", 3600,
                      [{"exercise": "Bench Press (Barbell)", "idx": k, "weight_kg": w, "reps": 6} for k in range(3)])
    for i in range(7):
        h.add_sleep("health_connect", f"t{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, 410)
        h.add_sleep("health_connect", f"l{i}", NOW - (i + 7) * DAY - 8 * 3600, NOW - (i + 7) * DAY, 380)
        day = datetime.fromtimestamp(NOW - i * DAY, timezone.utc).strftime("%Y-%m-%d")
        h.add_nutrition("cronometer_csv", f"n{i}", day, 2100, 85, 250, 60, 1800)
    led = Ledger(tmp_path / "f.db", clock=lambda: NOW)
    acct = led.add_account("Main")
    led.set_budget("food", 400000)
    led.add_manual(acct, "2026-10-04", -250000, "ZOMATO", "food")
    return h, led


def test_the_weekly_review_lists_wins_misses_and_one_suggestion_with_no_invented_numbers(tmp_path):
    h, led = seed(tmp_path)
    r = weekly_review(h, led, NOW, TODAY)
    assert r.wins and r.misses and r.suggestion
    assert r.text.count("One suggestion:") == 1
    numbers = set(re.findall(r"\d+(?:\.\d+)?", r.text))
    assert numbers <= r.facts, numbers - r.facts
    # every figure traces to the fixture: 2 of 4 sessions would be wrong (it is 2 this week); check the real one
    assert "of 4 planned" in r.text
    assert not re.search(r"(?i)\b(55|body weight|weigh)\b", r.text)  # body-neutral: the scale never appears


async def test_the_tool_path_gives_the_same_review(tmp_path):
    h, led = seed(tmp_path)
    plugin = make_host(tmp_path / "cfg", services={"health": h, "finance": led})
    text = await plugin.call("life_review", {})
    assert text == weekly_review(h, led, NOW, TODAY).text


async def test_quiet_hours_hold_every_push_this_package_adds(tmp_path):
    gw, fake, store, _ = build(tmp_path)
    data = store.config.model_dump(mode="json")
    data["coach"].update(enabled=True, briefs=True)
    data["life"].update(checkins=True, review_push=True)
    store.update(data)
    h, _ = seed(tmp_path / "d")
    push = PushRouter(gw, EventBus(), quiet=lambda now: True, state_path=tmp_path / "held.json",
                      clock=lambda: datetime(2026, 10, 18, 23, 30))
    life = LifeDelivery(gw, push, lambda: h)
    coach = CoachDelivery(gw, push, lambda: h)
    wid = h.rows("workouts")[0]["id"]
    results = [await life.handle({"type": "life", "kind": "checkin", "day": "2026-10-18"}),
               await life.handle({"type": "life", "kind": "review", "text": "Wins: a."}),
               await coach.handle({"type": "coach", "kind": "debrief", "text": "Good session.", "workout_id": wid}),
               await coach.handle({"type": "coach", "kind": "brief", "text": "Next up is Push."})]
    assert results == ["held", "held", "held", "held"]
    assert fake.sent == [] and len(push._held) == 3  # the check-in is simply skipped, the rest wait for morning
