"""The coach tools: small, sensitive facts packs from the engine; off until switched on and agreed (LF-07..LF-10)."""
from datetime import datetime

from tests.plugin_helpers import make_host
from minty_core.health import HealthHub

DAY = 86400.0
NOW = 1_800_000_000.0


def hub(tmp_path):
    h = HealthHub(tmp_path / "health.db", clock=lambda: NOW)
    for d in ("training", "sleep", "body", "nutrition", "activity"):
        h.grant(d)
    plan = [(20, "Push"), (18, "Pull"), (16, "Legs"), (13, "Push"), (11, "Pull"), (9, "Legs"), (6, "Push"),
            (4, "Pull"), (2, "Legs")]
    lifts = {"Push": ("Bench Press (Barbell)", 80), "Pull": ("Bent Over Row", 60), "Legs": ("Squat (Barbell)", 100)}
    for i, (d, title) in enumerate(plan):
        ex, w = lifts[title]
        h.add_workout("hevy", f"w{i}", NOW - d * DAY, title, 3600,
                      [{"exercise": ex, "idx": k, "weight_kg": w + i, "reps": 6} for k in range(4)])
    h.add_body("health_connect", "b1", NOW - 2 * DAY, "weight", 62.4, "kg")
    for i in range(1, 15):
        h.add_sleep("health_connect", f"s{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, 450)
    for i in range(14):
        day = datetime.fromtimestamp(NOW - (i + 1) * DAY).strftime("%Y-%m-%d")
        h.add_nutrition("cronometer_csv", f"n{i}", day, 2400, 95, 300, 70, 2200)
    return h


def host(tmp_path, h, **coach):
    cfg = {"enabled": True, **coach}
    plugin = make_host(tmp_path / "cfg", services={"health": h})
    data = plugin._store.config.model_dump(mode="json")
    data["coach"].update(cfg)
    plugin._store.update(data)
    return plugin


async def test_it_is_off_until_switched_on(tmp_path):
    h = hub(tmp_path)
    plugin = make_host(tmp_path / "cfg", services={"health": h})
    assert "switched off" in await plugin.call("coach_week", {})


async def test_the_week_summary_is_a_small_sensitive_pack_and_is_logged(tmp_path):
    h = hub(tmp_path)
    plugin = host(tmp_path, h)
    out = await plugin.call("coach_week", {})
    assert len(out) < 900 and "Hard sets" in out and "kg x" not in out  # muscles and maxes, not set-level rows
    assert plugin.get_tool("coach_week").sensitive is True
    assert h.last_advice()["topic"] == "week"


async def test_a_single_lift_shows_the_trend_and_the_next_target(tmp_path):
    h = hub(tmp_path)
    plugin = host(tmp_path, h)
    out = await plugin.call("coach_lift", {"exercise": "squat"})
    assert "Squat (Barbell)" in out and "estimated max" in out and "Next" in out
    assert "no lift" in (await plugin.call("coach_lift", {"exercise": "zzz"})).lower()


async def test_the_next_session_brief(tmp_path):
    h = hub(tmp_path)
    plugin = host(tmp_path, h)
    out = await plugin.call("coach_next", {})
    assert "Push" in out and "Warm-up" in out


async def test_food_needs_the_nutrition_switch_and_says_what_it_cannot_tell(tmp_path):
    h = hub(tmp_path)
    plugin = host(tmp_path, h, nutrition=False)
    assert "switched off" in await plugin.call("coach_food", {})
    on = host(tmp_path / "x", h, nutrition=True)
    out = await on.call("coach_food", {})
    assert "Protein" in out and "87" in out and "125" in out  # 1.4-2.0 g/kg of 62.4 kg
    assert "estimate" in out.lower() or "weight entries" in out


async def test_a_paused_coach_and_a_missing_agreement_say_so(tmp_path):
    h = hub(tmp_path)
    h.set_profile("coach_paused", "1")
    plugin = host(tmp_path, h)
    assert "paused" in await plugin.call("coach_week", {})
    h.set_profile("coach_paused", "0")
    h.revoke("training")
    assert "agreed" in await plugin.call("coach_week", {})


async def test_readiness_is_a_hint_with_its_caveat(tmp_path):
    h = hub(tmp_path)
    h.add_sleep("health_connect", "last", NOW - 9 * 3600, NOW - 3 * 3600, 300)
    plugin = host(tmp_path, h)
    out = await plugin.call("coach_readiness", {})
    assert "short on sleep" in out and "not a health score" in out


async def test_planning_tools_build_a_programme_and_answer_a_goal_honestly(tmp_path):
    h = hub(tmp_path)
    plugin = host(tmp_path, h)
    out = await plugin.call("coach_plan", {"what": "programme", "days": 4, "equipment": "dumbbell bands"})
    assert "Upper A" in out and "Barbell" not in out and "Hevy by hand" in out
    goal = await plugin.call("coach_plan", {"what": "goal", "exercise": "Squat (Barbell)", "target": "250",
                                            "weeks": "6"})
    assert "not realistic" in goal or "stretch" in goal or "enough data" in goal  # this fixture has 3 squat sessions
    assert (await plugin.call("coach_plan", {"what": "deload"})).startswith(("I don't see a reason", "A lighter week"))


async def test_review_tools_and_the_injury_log(tmp_path):
    h = hub(tmp_path)
    h.set_profile("sessions_per_week", "3")
    plugin = host(tmp_path, h)
    assert "estimated" in await plugin.call("coach_review", {"what": "prs"})
    assert "streak" in await plugin.call("coach_review", {"what": "consistency"})
    assert "active minutes" in await plugin.call("coach_review", {"what": "minutes"})
    assert "Squat" in await plugin.call("coach_review", {"what": "stall", "exercise": "Squat (Barbell)"})
    logged = await plugin.call("coach_injury", {"action": "log", "area": "knee", "note": "twinge"})
    assert "can't diagnose" in logged
    assert "cleared" in await plugin.call("coach_injury", {"action": "ramp", "id": 1})


async def test_planning_can_be_switched_off(tmp_path):
    h = hub(tmp_path)
    plugin = host(tmp_path, h, planning=False)
    assert "switched off" in await plugin.call("coach_plan", {"what": "programme"})


async def test_foods_suggestions_and_wellbeing_tools(tmp_path):
    h = hub(tmp_path)
    plugin = host(tmp_path, h)
    assert "Added" in await plugin.call("coach_foods", {"action": "adopt"})
    assert "Saved Ragi dosa" in await plugin.call("coach_foods", {"action": "add", "name": "Ragi dosa", "serving": "1",
                                                                  "kcal": "120", "protein": "3", "cost": "12"})
    h.set_profile("allergies", "peanut")
    out = await plugin.call("coach_eat_next", {"what": "gap", "grams": "25"})
    assert "From your own list" in out and "Peanuts" not in out
    assert "per 10 g protein" in await plugin.call("coach_eat_next", {"what": "cost"})
    assert "Recipe Dal rice" in await plugin.call("coach_foods", {"action": "recipe", "name": "Dal rice",
                                                                  "items": "Dal, cooked (1 bowl) x1; Rice, cooked (1 bowl) x1"})
    assert "Protein" in await plugin.call("coach_wellbeing", {"what": "weekly"}) or "protein" in \
        await plugin.call("coach_wellbeing", {"what": "weekly"})
    assert "sessions" in await plugin.call("coach_wellbeing", {"what": "progress"})
    off = host(tmp_path / "off", h, nutrition=False)
    assert "switched off" in await off.call("coach_wellbeing", {"what": "weekly"})
