"""Dashboards, the 60-second briefing, the weekly review, goals and check-ins (WP-25)."""
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from minty_core.finance import Ledger
from minty_core.health import HealthHub
from minty_core.life import (VIEWS, add_okr, briefing, checkin_summary, dashboard, okr_progress, save_checkin,
                             weekly_review)

DAY = 86400.0
NOW = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc).timestamp()  # a Sunday evening
TODAY = date(2026, 10, 18)


def hub(tmp_path, per_week="3"):
    h = HealthHub(tmp_path / "h.db", clock=lambda: NOW)
    for d in ("training", "sleep", "body", "nutrition", "activity"):
        h.grant(d)
    h.set_profile("sessions_per_week", per_week)
    h.add_body("health_connect", "w", NOW - DAY, "weight", 60.0, "kg")
    return h


def ledger(tmp_path):
    return Ledger(tmp_path / "f.db", clock=lambda: NOW)


def session(h, ext, days_ago, title, w, reps=5, sets=3):
    h.add_workout("hevy", ext, NOW - days_ago * DAY, title, 3600,
                  [{"exercise": "Squat (Barbell)", "idx": k, "weight_kg": w, "reps": reps} for k in range(sets)])


def fill_week(h, this_sleep=7.5, last_sleep=6.5, this_protein=100, last_protein=70):
    for i in range(7):
        h.add_sleep("health_connect", f"t{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, int(this_sleep * 60))
        h.add_sleep("health_connect", f"l{i}", NOW - (i + 7) * DAY - 8 * 3600, NOW - (i + 7) * DAY, int(last_sleep * 60))
        for tag, off, p in (("t", i, this_protein), ("l", i + 7, last_protein)):
            day = datetime.fromtimestamp(NOW - off * DAY, timezone.utc).strftime("%Y-%m-%d")
            h.add_nutrition("cronometer_csv", f"n{tag}{i}", day, 2300, p, 280, 70, 2000)


def test_there_are_three_views_and_each_is_a_few_lines(tmp_path):
    assert VIEWS == ("training", "recovery", "money")
    h, led = hub(tmp_path), ledger(tmp_path)
    session(h, "a", 1, "Legs", 100)
    session(h, "b", 3, "Legs", 102.5)
    fill_week(h)
    acct = led.add_account("Main")
    led.set_budget("food", 500000)
    led.add_manual(acct, "2026-10-05", -200000, "ZOMATO", "food")
    for view in VIEWS:
        card = dashboard(view, h, led, NOW, TODAY)
        assert 1 <= len(card.splitlines()) <= 5 and card
    assert "2 sessions" in dashboard("training", h, led, NOW, TODAY)
    assert "7.5 h" in dashboard("recovery", h, led, NOW, TODAY)
    money = dashboard("money", h, led, NOW, TODAY)
    assert "food" in money and "ZOMATO" not in money
    with pytest.raises(ValueError):
        dashboard("everything", h, led, NOW, TODAY)


def test_the_briefing_is_sixty_seconds_and_leaves_out_what_has_no_data(tmp_path):
    h, led = hub(tmp_path), ledger(tmp_path)
    session(h, "a", 1, "Legs", 100)
    fill_week(h)
    led.set_budget("food", 500000)
    text = briefing(h, led, NOW, TODAY)
    assert len(text.split()) <= 150 and "session" in text and "sleep" in text.lower()
    bare = briefing(HealthHub(tmp_path / "x.db", clock=lambda: NOW), ledger(tmp_path / "y"), NOW, TODAY)
    assert "nothing" in bare.lower() and len(bare.split()) < 30


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def test_the_weekly_review_lists_wins_misses_and_one_suggestion_from_the_numbers(tmp_path):
    h, led = hub(tmp_path), ledger(tmp_path)
    # this week: 2 sessions (target 3) with a squat PR; sleep 7.5 vs 6.5; protein 100 vs 70; food over budget
    session(h, "o1", 12, "Legs", 100)
    session(h, "o2", 10, "Legs", 100)
    session(h, "n1", 4, "Legs", 105)
    session(h, "n2", 2, "Legs", 110)
    fill_week(h)
    acct = led.add_account("Main")
    led.set_budget("food", 100000)
    led.add_manual(acct, "2026-10-12", -150000, "ZOMATO", "food")
    save_checkin(h, "2026-10-16", energy="high", priority_done=True)
    r = weekly_review(h, led, NOW, TODAY)
    assert any("estimated max" in w and "Squat" in w for w in r.wins)
    assert any("sleep" in w.lower() and "7.5" in w for w in r.wins)
    assert any("protein" in w.lower() for w in r.wins)
    assert any("food" in m and "over" in m for m in r.misses)
    assert any("2 of 3" in m for m in r.misses)  # sessions against target
    assert r.suggestion and r.suggestion.count(".") <= 3
    # nothing in the text is a number the structures do not hold
    assert numbers_in(r.text) <= r.facts
    assert "1 of 7 days" in r.text or "1 check-in" in r.text


def test_with_almost_nothing_logged_it_says_so_instead_of_inventing(tmp_path):
    r = weekly_review(hub(tmp_path), ledger(tmp_path), NOW, TODAY)
    assert r.wins == [] and "not enough" in r.text.lower() and numbers_in(r.text) <= r.facts


def test_goals_follow_a_metric_or_are_updated_by_hand_and_show_their_pace(tmp_path):
    h = hub(tmp_path)
    for i, (d, w) in enumerate(((40, 90), (26, 95), (12, 100), (2, 105))):
        session(h, f"s{i}", d, "Legs", w)
    gid = add_okr(h, "Get stronger", "Squat estimated max", 150, unit="kg", due="2027-01-18",
                  source="e1rm:Squat (Barbell)")
    mid = add_okr(h, "Be consistent", "Sessions in a week", 3, unit="sessions", due="2026-12-31", source="sessions_week")
    hand = add_okr(h, "Read more", "Books finished", 6, unit="books", due="2026-12-31", source="manual")
    h.update_okr(hand, 2)
    rows = {r["key_result"]: r for r in okr_progress(h, NOW, TODAY)}
    squat = rows["Squat estimated max"]
    assert squat["current"] == pytest.approx(105 * (1 + 5 / 30), abs=0.01) and squat["pct"] == round(122.5 / 150 * 100)
    assert squat["pace"] in ("on pace", "behind", "ahead") and squat["weeks_left"] == 13
    assert rows["Books finished"]["current"] == 2 and rows["Books finished"]["pct"] == 33
    assert rows["Sessions in a week"]["current"] == 1 and gid != mid


def test_check_ins_store_two_taps_and_are_summarised(tmp_path):
    h = hub(tmp_path)
    save_checkin(h, "2026-10-12", energy="low")
    save_checkin(h, "2026-10-12", priority_done=False)  # a second tap on the same day merges
    save_checkin(h, "2026-10-14", energy="high", priority_done=True)
    s = checkin_summary(h, "2026-10-12", "2026-10-18")
    assert s.days == 2 and s.energy == {"low": 1, "high": 1} and s.priority_done == 1 and s.priority_asked == 2
    with pytest.raises(ValueError):
        save_checkin(h, "2026-10-15", energy="amazing")
