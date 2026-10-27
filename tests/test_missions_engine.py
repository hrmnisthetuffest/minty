"""Missions: a goal worked in the background until the evidence, not the worker's word, says it is done."""
import json

from tests.agent_eval.scripted import ScriptedRouter
from minty_core.agentloop import Budget, StopJob
from minty_core.llm.types import ToolSpec
from minty_core.missions import MISSION_PROMPT, Ledger, run_mission

OBJ = {"type": "object", "properties": {}}
SPECS = [ToolSpec("notes_find", "find notes", OBJ), ToolSpec("mail_send", "send mail", OBJ)]


def tool(name, **args):
    return lambda last: ("tool", name, args)


def say(text):
    return lambda last: ("say", text)


class Tools:
    """A world of tools whose answers a test decides; every call is remembered."""

    def __init__(self, answers=None):
        self.answers, self.calls = answers or {}, []

    async def __call__(self, name, args):
        self.calls.append((name, args))
        answer = self.answers.get(name, "ok")
        return answer.pop(0) if isinstance(answer, list) else answer


def plan(*steps):
    return lambda user: json.dumps({"checkpoints": [{"goal": g, "criterion": c} for g, c in steps]})


async def mission(router, tools, goal="find my notes about the trip and say how many there are", **kw):
    return await run_mission(router, goal, specs=kw.pop("specs", SPECS), call_tool=tools, **kw)


async def test_a_mission_that_did_the_work_is_done_and_verified():
    router = ScriptedRouter([tool("notes_find", q="trip"), say("Found 3 notes about the trip.")])
    tools = Tools({"notes_find": "3 notes: a, b, c"})
    out = await mission(router, tools)
    assert (out.status, out.verified) == ("done", True) and "3 notes" in out.text
    assert tools.calls == [("notes_find", {"q": "trip"})]
    assert out.ledger[0]["tool"] == "notes_find" and out.ledger[0]["ok"] is True


async def test_the_verifier_is_shown_what_the_tools_returned_not_just_the_claim():
    seen = []
    router = ScriptedRouter([tool("notes_find", q="trip"), say("Found 3 notes.")],
                            verifier=lambda user: seen.append(user) or True)
    await mission(router, Tools({"notes_find": "3 notes: a, b, c"}))
    assert "3 notes: a, b, c" in seen[0] and "notes_find" in seen[0] and "not evidence" in seen[0]


async def test_saying_it_is_done_without_doing_anything_is_not_done():
    router = ScriptedRouter([say("Done! I emailed everyone.")],
                            verifier=lambda user: "No tool calls were made" not in user)
    out = await mission(router, Tools(), goal="email everyone on the list")
    assert out.status in ("blocked", "failed") and out.status != "done"
    assert out.verified is False


async def test_a_failed_tool_is_recovered_from_and_both_calls_are_on_record():
    router = ScriptedRouter([tool("notes_find", q="x"), tool("notes_find", q="y"), say("Found it.")])
    tools = Tools({"notes_find": ["Error: timed out", "1 note: z"]})
    out = await mission(router, tools)
    assert out.status == "done"
    assert [e["ok"] for e in out.ledger] == [False, True]


async def test_a_tool_the_worker_was_not_given_cannot_be_called():
    router = ScriptedRouter([tool("shell_run", command="rm -rf /"), say("ok")])
    tools = Tools()
    out = await mission(router, tools)
    assert tools.calls == [] and out.ledger[0]["ok"] is False and "not a tool" in out.ledger[0]["result"]


async def test_when_it_cannot_go_on_without_the_user_it_is_blocked_with_the_question():
    asked = []
    router = ScriptedRouter([say("")], verifier=lambda user: (False, "nothing shown"))
    out = await mission(router, Tools(), ask_user=asked.append)
    assert out.status == "blocked" and out.question and asked == [out.question]


async def test_running_out_of_budget_fails_honestly_and_keeps_the_plan_for_a_resume():
    router = ScriptedRouter([tool("notes_find", q="a")] * 50)
    tools = Tools({"notes_find": ["one", "two", "three", "four", "five", "six", "seven", "eight"]})
    out = await mission(router, tools, budget=Budget(steps=3, seconds=60, tokens=10**9))
    assert out.status == "failed" and "ran out" in out.text and out.verified is False
    assert json.loads(out.plan_json)["goal"]


async def test_a_declined_step_ends_the_mission_as_declined_not_as_a_crash():
    async def declining(name, args):
        raise StopJob("you said no to sending that")
    router = ScriptedRouter([tool("mail_send", to="a@b.c"), say("sent")])
    out = await mission(router, declining)
    assert out.status == "failed" and "you said no" in out.text


async def test_progress_is_saved_after_every_checkpoint_so_a_restart_loses_little():
    saved = []
    router = ScriptedRouter([tool("notes_find", q="a"), say("first done"), tool("notes_find", q="b"), say("second done")],
                            planner=plan(("look for a", "a was looked for"), ("look for b", "b was looked for")))
    goal = "look for a and then look for b, then tell me both results"
    out = await mission(router, Tools({"notes_find": "hit"}), goal=goal, save=saved.append)
    assert out.status == "done"
    assert len(saved) >= 3 and all({"plan", "ledger"} <= set(s) for s in saved)
    middle = [json.loads(s["plan"])["checkpoints"] for s in saved]
    assert any(cps[0]["passed"] and not cps[1]["passed"] for cps in middle)  # a state with only the first step finished


async def test_a_resumed_mission_does_only_what_is_left_and_rechecks_the_last_finished_step():
    done_plan = {"goal": "g", "checkpoints": [
        {"id": 1, "goal": "look for a", "criterion": "a looked for", "passed": True, "verified": True, "evidence": "e",
         "claim": "did a", "attempts": 0},
        {"id": 2, "goal": "look for b", "criterion": "b looked for", "passed": False, "verified": False, "evidence": "",
         "claim": "", "attempts": 0}]}
    checked = []
    router = ScriptedRouter([tool("notes_find", q="b"), say("b done")], verifier=lambda user: checked.append(user) or True)
    tools = Tools({"notes_find": "hit"})
    out = await mission(router, tools, goal="g", resume={"plan": json.dumps(done_plan), "ledger": []})
    assert out.status == "done" and tools.calls == [("notes_find", {"q": "b"})]
    assert "a looked for" in checked[0]  # the finished step was re-verified before building on it


async def test_the_users_answer_to_a_question_reaches_the_worker():
    router = ScriptedRouter([say("Booked for Friday.")])
    out = await mission(router, Tools(), goal="book the dentist", answer="Friday please", question="Which day?")
    assert "Friday please" in router.executor_inputs[0] and "Which day?" in router.executor_inputs[0]
    assert out.status == "done"


def test_the_worker_is_told_the_rules_that_matter():
    for must in ("never say something is done", "Error", "strangers", "only the user has"):
        assert must.lower() in MISSION_PROMPT.lower()


def test_the_ledger_is_bounded_and_round_trips():
    ledger = Ledger()
    for i in range(80):
        ledger.add("t", {"i": i}, "x" * 900, ok=True)
    assert len(ledger.entries) == 40 and len(ledger.entries[0]["result"]) <= 240
    again = Ledger.from_data(ledger.to_data())
    assert again.entries == ledger.entries
    assert Ledger.from_data("junk").entries == [] and Ledger.from_data([{"bad": 1}]).entries == []
