"""The advice log and the coach's spoken controls (LF-09): every piece of advice can be traced to its facts."""
from minty_core.coach.commands import CoachCommands
from minty_core.coach.safety import CoachSafety
from minty_core.health import HealthHub


def hub(tmp_path):
    return HealthHub(tmp_path / "health.db", clock=lambda: 1_800_000_000.0)


def test_advice_is_logged_with_its_facts_and_rules(tmp_path):
    h = hub(tmp_path)
    assert h.last_advice() is None
    ident = h.log_advice("progression", "Add a rep to squats next week.",
                         facts="Squat 3x5 at 100 kg, weakest set 5.", rules=["double progression: add a rep"])
    last = h.last_advice()
    assert last["id"] == ident and last["topic"] == "progression"
    assert last["facts"].startswith("Squat 3x5") and last["rules"] == ["double progression: add a rep"]
    h.log_advice("sleep", "Aim for 8 hours.", facts="avg 6.8 h", rules=["teen band 8-10 h"])
    assert h.last_advice()["topic"] == "sleep" and len(h.advice(limit=5)) == 2


def test_why_did_you_say_that_names_the_rule_and_the_numbers(tmp_path):
    h = hub(tmp_path)
    cmds = CoachCommands(h)
    assert "haven't given you any" in cmds.handle("why did you say that")
    h.log_advice("progression", "Add a rep.", facts="Squat 3x5 at 100 kg", rules=["double progression"])
    out = cmds.handle("why did you say that")
    assert "double progression" in out and "Squat 3x5 at 100 kg" in out
    assert cmds.handle("what was that based on") == out
    assert cmds.handle("what time is it") is None


def test_pause_and_resume_coaching_are_one_phrase_each(tmp_path):
    h = hub(tmp_path)
    cmds = CoachCommands(h)
    paused = lambda: h.profile().get("coach_paused") == "1"  # noqa: E731
    safety = CoachSafety(lambda: "adult", lambda: False, paused)
    assert safety.check("give me a workout plan") is None
    assert "paused" in cmds.handle("pause coaching").lower() and paused()
    assert safety.check("give me a workout plan").kind == "pause"
    assert "back" in cmds.handle("resume coaching").lower() and not paused()


def test_profile_accepts_the_coach_fields_and_rejects_nonsense(tmp_path):
    import pytest

    h = hub(tmp_path)
    h.set_profile("sessions_per_week", "4")
    h.set_profile("exam_week", "1")
    assert h.profile()["sessions_per_week"] == "4" and h.profile()["exam_week"] == "1"
    for key, bad in (("sessions_per_week", "0"), ("sessions_per_week", "40"), ("exam_week", "maybe")):
        with pytest.raises(ValueError):
            h.set_profile(key, bad)
