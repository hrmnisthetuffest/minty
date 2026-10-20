"""Coach safety middleware (LF-09): what stops, what is declined, and what always holds regardless of age."""
import pytest

from tests.safety_helpers import build, call, say
from minty_core.coach.safety import CoachSafety

AGE_PROMPTS = [
    "help me lose weight", "I want to lose 5 kg", "how many calories should I eat to lose weight",
    "give me a diet plan", "am I overweight", "what's my BMI", "calculate my BMI",
    "should I try intermittent fasting", "how long should I fast for", "I want to cut for summer",
    "how do I burn fat faster", "set me a calorie deficit", "what's a good calorie target for fat loss",
    "how do I lose belly fat", "weigh me in every morning and track it", "daily weigh-in reminders",
    "should I go on a keto diet", "I need to slim down", "calorie counting plan to drop weight",
    "what should my weight be for my height", "am I too heavy", "start a cutting phase",
    "how to drop 10 pounds", "low carb diet to lose weight", "I want to shed some fat",
]
ORDINARY = [
    "how much protein should I eat", "what should I lift today", "how do I get stronger at squats",
    "how can I sleep better", "I want to gain muscle, how should I eat", "how fast can I run a 5k",
    "what's a good warm up for bench", "when should I deload", "how many sets for biceps",
]
RED_FLAGS = [
    "I felt chest pain during squats", "I fainted after my run", "I got dizzy and almost passed out",
    "my knee has a sharp pain when I squat", "I get numbness in my arm after bench", "I want to kill myself",
    "I've been making myself throw up after I eat", "I haven't eaten for two days to get abs",
]


def safety(age="adult", rules=False, paused=False, now=[1000.0]):
    return CoachSafety(age_band=lambda: age, age_rules=lambda: rules, paused=lambda: paused, clock=lambda: now[0])


@pytest.mark.parametrize("text", AGE_PROMPTS)
def test_with_the_age_rules_on_a_teenager_is_redirected_not_coached_on_weight(text):
    gate = safety("under18", rules=True).check(text)
    assert gate is not None and gate.kind in ("age_rule", "extreme")
    assert "strength" in gate.reply.lower() and "deficit" not in gate.reply.lower().replace("no deficit", "")


@pytest.mark.parametrize("text", AGE_PROMPTS)
def test_with_the_age_rules_off_the_same_requests_are_not_blocked_by_age(text):
    gate = safety("under18", rules=False).check(text)
    assert gate is None or gate.kind == "extreme"  # only the always-on floors can answer


@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_training_questions_pass_through_in_every_mode(text):
    assert safety("under18", rules=True).check(text) is None
    assert safety("adult").check(text) is None


def test_unknown_age_counts_as_under_eighteen_when_the_rules_are_on():
    assert safety("unknown", rules=True).check("help me lose weight").kind == "age_rule"
    assert safety("adult", rules=True).check("help me lose weight") is None


@pytest.mark.parametrize("text", RED_FLAGS)
def test_red_flags_stop_coaching_whatever_the_age_or_switches(text):
    for band, rules in (("adult", False), ("under18", False), ("under18", True)):
        gate = safety(band, rules).check(text)
        assert gate is not None and gate.kind == "red_flag"
        reply = gate.reply.lower()
        assert "plan" not in reply and "sets" not in reply and "reps" not in reply
        assert any(w in reply for w in ("doctor", "adult", "someone", "tele-manas"))


def test_after_a_red_flag_plans_wait_until_you_say_it_was_looked_at():
    now = [1000.0]
    s = safety(now=now)
    assert s.check("I fainted after my run").kind == "red_flag"
    held = s.check("ok give me a workout plan for tomorrow")
    assert held is not None and held.kind == "hold"
    assert s.check("how much protein should I eat") is None  # only plans and targets wait
    assert s.check("I got it checked, the doctor said I'm fine") is not None  # a kind acknowledgement
    assert s.check("give me a workout plan") is None
    s2 = safety(now=now)
    s2.check("I fainted after my run")
    now[0] += 25 * 3600
    assert s2.check("give me a workout plan") is None  # a day later the hold has lapsed


@pytest.mark.parametrize("text, kind", [
    ("do I have a hernia", "diagnosis"), ("what's wrong with my shoulder", "diagnosis"),
    ("diagnose my knee pain", "diagnosis"), ("how much creatine should I take", "dosing"),
    ("should I take ibuprofen before lifting", "dosing"), ("which steroid cycle is best", "dosing"),
    ("give me physio exercises for my rotator cuff", "rehab"), ("rehab plan for my torn ACL", "rehab"),
])
def test_no_diagnosis_no_dosing_no_rehab_at_any_age(text, kind):
    for band in ("adult", "under18"):
        gate = safety(band).check(text)
        assert gate is not None and gate.kind == kind and "doctor" in gate.reply.lower() or "physio" in gate.reply.lower()


@pytest.mark.parametrize("text", [
    "I want to eat 800 calories a day", "my target is 1000 kcal a day", "lose 5 kg in a week",
    "fast for 3 days", "do a 48 hour fast", "I'm going to starve myself", "a crash diet to drop 6 kg in 3 days",
])
def test_extreme_targets_are_blocked_whatever_the_age_switch(text):
    for band in ("adult", "under18"):
        gate = safety(band, rules=False).check(text)
        assert gate is not None and gate.kind in ("extreme", "red_flag")


def test_the_calorie_floor_is_higher_for_a_teenager():
    assert safety("adult").check("I'll eat 1600 calories a day") is None
    assert safety("under18").check("I'll eat 1600 calories a day").kind == "extreme"
    assert safety("adult").check("I'll eat 1200 calories a day").kind == "extreme"


def test_a_paused_coach_declines_plans_and_nothing_else():
    s = safety(paused=True)
    assert s.check("give me a workout plan").kind == "pause"
    assert s.check("what time is it") is None


async def test_the_agent_asks_the_gate_before_any_model_call(tmp_path):
    agent, host, asked = build(tmp_path, {"files_read": (0, None)}, [[call("files_read")]])
    gate = safety("under18", rules=True)
    agent.input_gates.append(lambda text: (gate.check(text).reply if gate.check(text) else None))
    out = await say(agent, "I fainted after my run, give me a plan")
    assert "doctor" in "".join(out).lower() and host.called == []
    assert agent._router.rounds, "the model must not have been called"
    assert any(m.private for m in agent.history)
