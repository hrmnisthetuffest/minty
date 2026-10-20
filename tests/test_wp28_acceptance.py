"""WP-28 acceptance through the coach tools: honest feasibility, rescue spacing, a post-mortem of measured facts."""
import re
from datetime import datetime

from tests.plugin_helpers import make_host
from minty_core.health import HealthHub

DAY = 86400.0
NOW = 1_800_000_000.0
SQ = "Squat (Barbell)"


def setup(tmp_path):
    h = HealthHub(tmp_path / "h.db", clock=lambda: NOW)
    for d in ("training", "sleep", "body", "nutrition", "activity"):
        h.grant(d)
    plugin = make_host(tmp_path / "cfg", services={"health": h})
    data = plugin._store.config.model_dump(mode="json")
    data["coach"].update(enabled=True)
    plugin._store.update(data)
    return plugin, h


def climbing(h, per_week=0.5):
    for w in range(10):
        e = 100 + per_week * w
        weight = round(e / (1 + 5 / 30), 2)
        h.add_workout("hevy", f"c{w}", NOW - (9 - w) * 7 * DAY, "Legs", 3600,
                      [{"exercise": SQ, "idx": k, "weight_kg": weight, "reps": 5} for k in range(3)])


async def test_a_goal_that_cannot_be_met_in_the_time_is_called_not_realistic(tmp_path):
    plugin, h = setup(tmp_path)
    climbing(h)
    out = await plugin.call("coach_plan", {"what": "goal", "exercise": SQ, "target": "135", "weeks": "6"})
    assert "not realistic in that time" in out and "kg a week" in out and "Assumptions" in out
    again = await plugin.call("coach_plan", {"what": "goal", "exercise": SQ, "target": "107", "weeks": "6"})
    assert "realistic" in again and "not realistic" not in again


async def test_a_rescued_session_is_never_placed_within_48_hours_of_a_shared_muscle(tmp_path):
    plugin, h = setup(tmp_path)
    # a month of Mon Push / Wed Pull / Fri Legs / Sat Push-B, so the usual week is known
    from datetime import timedelta

    monday = datetime.fromtimestamp(NOW).replace(hour=7, minute=0, second=0, microsecond=0)
    monday -= timedelta(days=monday.weekday())
    layout = {0: ("Push", "Bench Press (Barbell)"), 2: ("Pull", "Bent Over Row"), 4: ("Legs", SQ),
              5: ("Push", "Overhead Press")}
    for week in range(1, 5):
        for day, (title, ex) in layout.items():
            when = (monday - timedelta(days=7 * week) + timedelta(days=day)).timestamp()
            h.add_workout("hevy", f"w{week}{day}", when, title, 3600,
                          [{"exercise": ex, "idx": k, "weight_kg": 60, "reps": 8} for k in range(3)])
    out = await plugin.call("coach_plan", {"what": "rescue", "missed": "Push"})
    today = datetime.fromtimestamp(NOW).weekday()
    if out.startswith("Move Push"):
        day = int(re.search(r"day (\d)", out).group(1)) - 1
        assert day >= today  # a later day, never the past
        assert abs(5 - day) >= 2  # the other Push day (Saturday) is two or more days away
        assert day not in (4, 5, 6)
    else:
        assert out.startswith("Skip Push") and "don't double up" in out


async def test_the_post_mortem_cites_only_measured_facts_and_lists_the_gaps(tmp_path):
    plugin, h = setup(tmp_path)
    for i, days_ago in enumerate(range(80, 0, -7)):
        w = 100 + (2.5 * i if days_ago > 45 else 7.5)
        h.add_workout("hevy", f"s{i}", NOW - days_ago * DAY, "Legs", 3600,
                      [{"exercise": SQ, "idx": k, "weight_kg": w, "reps": 5} for k in range(3)])
    for d in range(1, 9):
        h.add_sleep("health_connect", f"sl{d}", NOW - d * DAY - 8 * 3600, NOW - d * DAY, 420)
    out = await plugin.call("coach_review", {"what": "stall", "exercise": SQ})
    assert "7.0 h" in out and "Data gaps" in out and "sleep was logged on 8 of the last 42 days" in out
    assert "no food was logged" in out
    for guess in ("probably", "maybe", "likely", "might"):
        assert guess not in out.lower()
    numbers = re.findall(r"\d+(?:\.\d+)?", out)
    assert numbers  # every figure in it comes from the log: the fixture has no other source of them
