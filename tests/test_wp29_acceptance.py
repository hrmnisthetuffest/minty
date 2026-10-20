"""WP-29 acceptance through the coach tools: body-neutral language everywhere, own foods only, honest normal ranges."""
import re
from datetime import datetime

import pytest

from tests.plugin_helpers import make_host
from minty_core.health import HealthHub

DAY = 86400.0
NOW = 1_800_000_000.0
LINT = re.compile(r"(?i)\b(deficit|weight[- ]loss|lose weight|losing weight|fast(?:ing)?\b(?! as)|weigh[- ]?in|bmi|"
                  r"diet plan|cutting|burn fat|calorie target|fat loss|slim)\b")


def setup(tmp_path, age):
    h = HealthHub(tmp_path / "h.db", clock=lambda: NOW)
    for d in ("training", "sleep", "body", "nutrition", "activity"):
        h.grant(d)
    if age:
        h.set_profile("age", age)
    h.add_body("health_connect", "w", NOW - DAY, "weight", 58.0, "kg")
    for i in range(1, 29):
        h.add_sleep("health_connect", f"s{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, 400 + (20 if i % 2 else -20))
        day = datetime.fromtimestamp(NOW - i * DAY).strftime("%Y-%m-%d")
        h.add_nutrition("cronometer_csv", f"n{i}", day, 2300, 70 + (i % 5), 280, 70, 2100)
    for w in range(1, 6):
        h.add_workout("hevy", f"w{w}", NOW - w * 3 * DAY, "Legs", 3600,
                      [{"exercise": "Squat (Barbell)", "idx": 0, "weight_kg": 90 + w, "reps": 5}])
    plugin = make_host(tmp_path / "cfg", services={"health": h})
    data = plugin._store.config.model_dump(mode="json")
    data["coach"].update(enabled=True, nutrition=True)
    plugin._store.update(data)
    return plugin, h


CALLS = [("coach_foods", {"action": "adopt"}), ("coach_foods", {"action": "list"}),
         ("coach_eat_next", {"what": "gap", "grams": "30"}), ("coach_eat_next", {"what": "vegetarian"}),
         ("coach_eat_next", {"what": "cost"}), ("coach_food", {}),
         *[("coach_wellbeing", {"what": w, "days_until": 6}) for w in
           ("macros", "morning", "weekly", "bedtime", "banking", "load", "ranges", "progress")]]


@pytest.mark.parametrize("age", ["15", "", "34"])
async def test_nothing_in_this_pack_uses_diet_or_scale_language_at_any_age(tmp_path, age):
    plugin, h = setup(tmp_path, age)
    seen = 0
    for name, args in CALLS:
        out = await plugin.call(name, args)
        seen += 1
        assert not out.startswith("Error"), (name, args, out)
        assert not LINT.search(out), (name, args, out)
    assert seen == len(CALLS)


async def test_suggestions_come_only_from_your_own_list_and_respect_allergies(tmp_path):
    plugin, h = setup(tmp_path, "15")
    assert "From your own list" not in await plugin.call("coach_eat_next", {"what": "gap", "grams": "30"})  # empty
    await plugin.call("coach_foods", {"action": "adopt"})
    h.set_profile("allergies", "peanut, milk")
    h.set_profile("diet", "vegetarian")
    out = await plugin.call("coach_eat_next", {"what": "gap", "grams": "30"})
    names = {f["name"] for f in h.foods()}
    assert "From your own list" in out
    for banned in ("Peanuts", "Paneer", "Curd", "Milk", "Whey", "Egg", "Chicken"):
        assert banned not in out, banned
    assert any(n in out for n in names)


async def test_normal_ranges_compare_with_your_own_baseline_and_state_how_much_data(tmp_path):
    plugin, h = setup(tmp_path, "15")
    h.add_sleep("health_connect", "last", NOW - 9 * 3600, NOW - 3600, 300)
    out = await plugin.call("coach_wellbeing", {"what": "ranges"})
    assert "your usual is 6.7 h" in out and "nights (28 days)" in out and "5.0 h" in out and "below" in out
    sparse = HealthHub(tmp_path / "s.db", clock=lambda: NOW)
    sparse.grant("sleep")
    for i in range(1, 5):
        sparse.add_sleep("health_connect", f"s{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, 400)
    from minty_core.coach.wellbeing import normal_ranges

    assert "Not enough data" in normal_ranges(sparse, NOW) and "4 nights" in normal_ranges(sparse, NOW)
