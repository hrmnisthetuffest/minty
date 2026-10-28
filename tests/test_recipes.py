"""Recipes: a goal that runs by itself on a schedule, once a day at most, and stops itself when it keeps failing."""
from datetime import datetime, timedelta

import pytest

from minty_core.recipes import RecipeError, Recipes

DAY = datetime(2026, 10, 28, 9, 0)  # a Wednesday


class Starter:
    def __init__(self):
        self.started, self.next_id = [], 1

    def __call__(self, goal, extra):
        self.started.append((goal, extra))
        self.next_id += 1
        return self.next_id - 1


def make(tmp_path, **kw):
    start = Starter()
    return Recipes(tmp_path / "recipes.sqlite", start=start, **kw), start


def at(h, m=0, day=0):
    return DAY.replace(hour=h, minute=m) + timedelta(days=day)


def test_a_recipe_runs_once_when_its_time_has_come_and_not_before(tmp_path):
    rec, start = make(tmp_path)
    rec.create("daily-reel", "make and queue today's reel", "18:00")
    assert rec.tick(at(17, 59)) == [] and start.started == []
    ran = rec.tick(at(18, 5))
    assert [r.recipe for r in ran] == ["daily-reel"] and start.started[0][0] == "make and queue today's reel"
    assert start.started[0][1]["recipe"] == "daily-reel" and start.started[0][1]["run"] > 0
    assert rec.tick(at(18, 30)) == [] and len(start.started) == 1  # once a day


def test_it_runs_again_the_next_day(tmp_path):
    rec, start = make(tmp_path)
    rec.create("r", "do it", "18:00")
    rec.tick(at(18, 1))
    rec.finished(1, "done", "ok")
    rec.tick(at(18, 1, day=1))
    assert len(start.started) == 2


def test_a_run_missed_because_the_computer_was_off_is_skipped_not_made_up_at_3am(tmp_path):
    rec, start = make(tmp_path, catch_up_minutes=120)
    rec.create("r", "do it", "18:00")
    assert rec.tick(at(22, 0)) == [] and start.started == []
    (run,) = rec.runs("r")
    assert run.status == "skipped" and "off" in run.summary
    assert rec.tick(at(22, 30)) == []  # recorded once, not again every tick


def test_days_of_the_week_and_disabled_recipes(tmp_path):
    rec, start = make(tmp_path)
    rec.create("weekends", "do it", "10:00", days="sat,sun")
    rec.create("off", "do it", "10:00")
    rec.pause("off", "paused by you")
    assert rec.tick(at(10, 5)) == []  # wednesday
    assert [r.recipe for r in rec.tick(at(10, 5, day=3))] == ["weekends"]  # saturday


def test_a_new_run_does_not_start_while_the_last_one_is_still_going(tmp_path):
    rec, start = make(tmp_path, catch_up_minutes=24 * 60)
    rec.create("r", "do it", "08:00", max_runs_per_day=3)
    rec.tick(at(9, 0))
    assert rec.tick(at(9, 30)) == [] and len(start.started) == 1  # run 1 has not finished
    rec.finished(1, "done", "ok")
    assert len(rec.tick(at(9, 40))) == 1  # now a second one is allowed (cap 3 a day)
    rec.finished(2, "done", "ok")
    rec.tick(at(9, 50))
    rec.finished(3, "done", "ok")
    assert rec.tick(at(10, 0)) == [] and len(start.started) == 3  # cap reached


def test_three_failures_in_a_row_pause_it_and_it_says_why(tmp_path):
    rec, start = make(tmp_path)
    rec.create("r", "do it", "18:00", max_failures=3)
    for day in range(3):
        rec.tick(at(18, 1, day=day))
        rec.finished(day + 1, "failed", "the render did not pass its checks")
    info = rec.get("r")
    assert info.paused and "3 times in a row" in info.paused_reason and "render did not pass" in info.paused_reason
    assert rec.tick(at(18, 1, day=3)) == []  # no more runs until the user says
    rec.resume("r")
    assert len(rec.tick(at(18, 1, day=4))) == 1 and rec.get("r").failures == 0


def test_a_success_resets_the_count_and_a_question_is_not_a_failure(tmp_path):
    rec, _ = make(tmp_path)
    rec.create("r", "do it", "18:00", max_failures=2)
    rec.tick(at(18, 1))
    rec.finished(1, "failed", "x")
    rec.tick(at(18, 1, day=1))
    rec.finished(2, "done", "fine")
    assert rec.get("r").failures == 0
    rec.tick(at(18, 1, day=2))
    rec.finished(3, "blocked", "Which clip?")
    assert rec.get("r").failures == 0 and rec.runs("r")[0].status == "blocked"


def test_validation_is_strict(tmp_path):
    rec, _ = make(tmp_path)
    for bad in [dict(name="", goal="g", at="18:00"), dict(name="Has Space", goal="g", at="18:00"),
                dict(name="ok", goal="", at="18:00"), dict(name="ok", goal="g" * 1500, at="18:00"),
                dict(name="ok", goal="g", at="6pm"), dict(name="ok", goal="g", at="25:00"),
                dict(name="ok", goal="g", at="18:00", days="funday"), dict(name="ok", goal="g", at="18:00", max_runs_per_day=0)]:
        with pytest.raises(RecipeError):
            rec.create(**bad)
    rec.create("ok", "g", "18:00")
    with pytest.raises(RecipeError, match="already"):
        rec.create("ok", "g2", "19:00")


def test_a_run_cut_off_by_a_restart_is_marked_interrupted_and_recipes_survive(tmp_path):
    rec, _ = make(tmp_path)
    rec.create("r", "do it", "18:00")
    rec.tick(at(18, 1))
    again, start2 = make(tmp_path)
    assert again.runs("r")[0].status == "interrupted" and again.get("r").goal == "do it"
    assert again.tick(at(18, 20)) == []  # today's run happened (it was cut off); no second try


def test_delete_removes_it_and_its_schedule(tmp_path):
    rec, start = make(tmp_path)
    rec.create("r", "do it", "18:00")
    assert rec.delete("r") and not rec.delete("r")
    assert rec.tick(at(18, 1)) == [] and rec.get("r") is None


def test_a_paused_recipe_and_a_failed_start_are_open_loops(tmp_path):
    rec, _ = make(tmp_path)
    rec.create("r", "do it", "18:00", max_failures=1)
    rec.tick(at(18, 1))
    rec.finished(1, "failed", "no clips left")
    (loop,) = rec.loops(now=at(19, 0).timestamp())
    assert loop.key == "recipe:r:paused" and "no clips left" in loop.text and "resume" in loop.text.lower()
    rec.resume("r")
    assert rec.loops(now=at(19, 0).timestamp()) == []


def test_a_start_that_raises_is_a_failed_run_not_a_crash(tmp_path):
    def broken(goal, extra):
        raise RuntimeError("no model")

    rec = Recipes(tmp_path / "x.sqlite", start=broken)
    rec.create("r", "do it", "18:00")
    rec.tick(at(18, 1))
    assert rec.runs("r")[0].status == "failed" and "no model" in rec.runs("r")[0].summary
