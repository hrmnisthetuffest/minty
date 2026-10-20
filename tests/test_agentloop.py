import json

import pytest

from tests.agent_eval.scripted import ScriptedRouter
from minty_core.agentloop import (
    AgentLoop, Budget, Checkpoint, Environment, NeedsHuman, Plan, StopJob, StuckDetector, Verdict, fingerprint, needs_plan,
    parse_plan, single_step_plan, trim_old_observations,
)
from minty_core.llm.types import Message, ToolSpec

OBJ = {"type": "object", "properties": {}}
SPECS = [ToolSpec("go", "open a page", OBJ), ToolSpec("noop", "do nothing", OBJ)]


def go(page):
    return lambda last: ("tool", "go", {"page": page})


def say(text):
    return lambda last: ("say", text)


class World:
    """A tiny world: `go` moves to a page; evidence is where we are and what we have seen."""

    def __init__(self):
        self.page, self.seen, self.calls = "start", [], []

    async def call(self, name, args):
        self.calls.append((name, args))
        if name == "go":
            self.page = args["page"]
            self.seen.append(self.page)
            return f"Page: {self.page}\nElements: [e1] link next"
        return f"Page: {self.page}\nElements: [e1] link next"  # noop: nothing changes

    async def evidence(self):
        return f"on page {self.page}; visited {self.seen}"

    def env(self, **kw):
        return Environment(system="You operate a tiny world.", specs=SPECS, call=self.call,
                           evidence=self.evidence, left_as="The world is left as it was.", **kw)


def plan_json(*items):
    return json.dumps({"checkpoints": [{"goal": g, "criterion": c} for g, c in items]})


# ---- A2.1 core types ------------------------------------------------------------------------------------
@pytest.mark.parametrize("goal, plan", [
    ("open the news page", False),
    ("open the first result", False),
    ("go to the next page", False),
    ("find the cheapest lamp", False),
    ("find the cheapest lamp and then add it to the cart", True),
    ("compare the desk lamp and the floor lamp", True),
    ("open the shop, search for a mug, and sort by price", True),
    ("search for lamps and sort them and open the first", True),
    (" ".join(["word"] * 19), True),
])
def test_complexity_gate(goal, plan):
    assert needs_plan(goal) is plan


def test_plan_round_trip_and_only_verdicts_set_passed(tmp_path):
    plan = Plan("buy lamp", [Checkpoint(1, "open shop", "url has /shop"), Checkpoint(2, "add lamp", "cart has 1")])
    snapshot = plan.checkpoints
    snapshot[0].passed = True  # editing a snapshot changes nothing
    assert not plan.checkpoints[0].passed and plan.current().id == 1
    plan.record(1, Verdict(True, "ok"), evidence="at /shop", claim="opened")
    assert plan.current().id == 2 and plan.checkpoints[0].verified
    plan.record(2, Verdict(True, "unchecked", verified=False))
    assert plan.current() is None and not plan.checkpoints[1].verified
    path = tmp_path / "plan.json"
    plan.save(path)
    again = Plan.load(path)
    assert again.goal == "buy lamp" and [c.passed for c in again.checkpoints] == [True, True]
    assert Plan.load(tmp_path / "missing.json") is None
    assert plan.render(None).startswith("Plan: [x] 1. open shop; [x] 2. add lamp")


def test_replace_current_renumbers():
    plan = Plan("g", [Checkpoint(1, "a", "ca", passed=True), Checkpoint(2, "b", "cb"), Checkpoint(3, "c", "cc")])
    plan.replace_current([Checkpoint(0, "b1", "c1"), Checkpoint(0, "b2", "c2")])
    assert [(c.id, c.goal) for c in plan.checkpoints] == [(1, "a"), (2, "b1"), (3, "b2"), (4, "c")]


@pytest.mark.parametrize("text", [
    "", "not json", "[]", '{"checkpoints": []}', '{"checkpoints": "x"}', '{"checkpoints": [{"goal": "a"}]}',
    json.dumps({"checkpoints": [{"goal": f"g{i}", "criterion": "c"} for i in range(9)]}),
    '{"checkpoints": ["just a string"]}',
])
def test_bad_plans_are_rejected(text):
    assert parse_plan(text, "goal") is None


def test_plan_is_found_inside_prose_and_fences():
    text = 'Sure! ```json\n' + plan_json(("open", "url ok"), ("read", "price seen")) + '\n``` done'
    plan = parse_plan(text, "goal")
    assert [c.goal for c in plan.checkpoints] == ["open", "read"]


def test_stuck_detector():
    d = StuckDetector(repeats=3, no_news=6)
    assert d.observe("click:a", "p1") == "" and d.observe("click:a", "p1") == ""
    assert "no effect 3 times" in d.observe("click:a", "p1")
    d = StuckDetector(repeats=3, no_news=4)
    assert d.observe("a", "p1") == ""
    reasons = [d.observe(f"x{i}", "p1") for i in range(5)]  # different actions, page never changes
    assert reasons[:3] == ["", "", ""] and "nothing new" in reasons[3]
    d = StuckDetector()
    assert [d.observe("scroll", f"page{i}") for i in range(8)] == [""] * 8  # a changing page is progress


def test_budget_and_trim_and_fingerprint():
    b = Budget(steps=2, tokens=100)
    assert b.exhausted() == ""
    b.used_steps = 2
    assert b.exhausted() == "steps"
    b = Budget(tokens=10)
    b.charge(80)
    assert b.exhausted() == "tokens"
    msgs = [Message("tool", f"Page {i}\nurl\nlots of text") for i in range(4)]
    trimmed = trim_old_observations(msgs)
    assert "trimmed" in trimmed[0].content and trimmed[3].content.endswith("lots of text")
    assert fingerprint("a  b\n c") == fingerprint("a b c") != fingerprint("a b d")


# ---- A2.2 / A2.3 the loop -------------------------------------------------------------------------------
async def test_fast_path_makes_no_planner_call_and_is_verified():
    world = World()
    router = ScriptedRouter([go("news"), say("The news page is open.")])
    result = await AgentLoop(router, world.env()).run("open the news page")
    assert result.status == "done" and result.verified and result.summary == "The news page is open."
    assert router.calls == {"executor": 2, "planner": 0, "verifier": 1}
    assert result.steps == 2 and result.tokens_est > 0  # verifier and planner calls are not "steps"


async def test_multi_step_plan_runs_checkpoint_by_checkpoint():
    world = World()
    router = ScriptedRouter(
        [go("shop"), say("Shop is open."), go("cart"), say("Cart is open."), say("All done: lamp is in the cart.")],
        planner=lambda user: plan_json(("open the shop", "on page shop"), ("open the cart", "on page cart"),
                                       ("report", "answer given")),
        verifier=lambda user: True)
    ticks = []
    result = await AgentLoop(router, world.env(), progress=ticks.append).run("open the shop and then the cart")
    assert result.status == "done" and result.summary == "All done: lamp is in the cart."
    assert [c.passed for c in result.plan.checkpoints] == [True, True, True]
    assert router.calls["planner"] == 1 and router.calls["verifier"] == 3
    assert "step 2 of 3: open the cart" in ticks
    # each checkpoint starts from the plan line and what earlier checkpoints found, not from chat history
    assert "[x] 1. open the shop" in router.executor_inputs[2] and "Shop is open." in router.executor_inputs[2]


async def test_a_false_done_is_rejected_by_the_evidence_and_retried_with_the_reason():
    world = World()
    seen_by_verifier = []

    def verifier(user):
        seen_by_verifier.append(user)
        ok = "cart" in world.seen
        return (ok, "the cart was never opened" if not ok else "cart visited")

    router = ScriptedRouter([say("Done, it is in the cart."), go("cart"), say("Opened the cart.")], verifier=verifier)
    result = await AgentLoop(router, world.env()).run("open the cart")
    assert result.status == "done" and result.verified
    assert "Done, it is in the cart." in seen_by_verifier[0] and "not evidence" in seen_by_verifier[0]
    assert "the cart was never opened" in router.executor_inputs[1]  # the retry was told why
    assert result.plan.checkpoints[0].attempts == 1


async def test_endless_failure_ends_in_one_specific_question():
    world = World()
    router = ScriptedRouter([go("a"), say("did it"), go("b"), say("did it"), go("c"), say("did it"),
                             go("d"), say("did it")],
                            verifier=lambda user: (False, "nothing matches"))
    asked = []
    result = await AgentLoop(router, world.env(), ask_user=asked.append).run("open the cart")
    assert result.status == "needs_user" and asked == [result.question]
    assert "open the cart" in result.question and "nothing matches" in result.question
    assert router.calls["verifier"] == 3  # retry, replan-or-retry, then ask: not forever


async def test_second_failure_replans_the_step():
    world = World()
    replans = []

    def planner(user):
        replans.append(user)
        return plan_json(("open the shop first", "on page shop"), ("then the cart", "on page cart"))

    verdicts = iter([(False, "wrong page"), (False, "still wrong"), (True, "ok"), (True, "ok")])
    router = ScriptedRouter([go("x"), say("hm"), go("y"), say("hm"), go("shop"), say("shop"), go("cart"), say("cart")],
                            planner=planner, verifier=lambda user: next(verdicts))
    result = await AgentLoop(router, world.env()).run("open the cart")
    assert result.status == "done" and len(replans) == 1 and "still wrong" in replans[0]
    assert [c.goal for c in result.plan.checkpoints] == ["open the shop first", "then the cart"]


async def test_a_spin_ends_stuck_quickly_with_a_clear_message():
    world = World()
    router = ScriptedRouter([lambda last: ("tool", "noop", {})])  # forever
    result = await AgentLoop(router, world.env()).run("open the captcha page")
    assert result.status == "stuck" and result.steps <= 4
    assert "got stuck" in result.summary and "no effect 3 times" in result.summary
    assert result.summary.endswith("The world is left as it was.")


async def test_budget_exhaustion_reports_what_was_done_and_what_is_left():
    world = World()
    pages = iter(f"p{i}" for i in range(100))
    router = ScriptedRouter(
        [go("shop"), say("shop open")] + [lambda last: ("tool", "go", {"page": next(pages)})],
        planner=lambda user: plan_json(("open the shop", "on page shop"), ("find the lamp", "lamp seen")),
        verifier=lambda user: True)
    result = await AgentLoop(router, world.env(), budget=Budget(steps=5)).run("open the shop then find the lamp")
    assert result.status == "budget" and "ran out of steps" in result.summary
    assert "Done: open the shop." in result.summary and "Still to do: find the lamp." in result.summary


async def test_unavailable_verifier_is_reported_honestly():
    world = World()
    router = ScriptedRouter([go("news"), say("Opened.")], verifier=lambda user: None)
    result = await AgentLoop(router, world.env()).run("open the news page")
    assert result.status == "done" and not result.verified
    assert result.summary == "Opened. (I could not double-check that.)"


async def test_a_declined_action_ends_the_job_and_is_not_retried():
    world = World()

    async def call(name, args):
        raise StopJob("You said no, so I stopped before pressing Place order.")

    env = Environment(system="s", specs=SPECS, call=call, evidence=world.evidence)
    router = ScriptedRouter([go("checkout")])
    result = await AgentLoop(router, env).run("place the order")
    assert result.status == "declined" and "You said no" in result.summary and router.calls["verifier"] == 0


async def test_escalates_once_to_the_stronger_model_before_asking():
    world = World()
    used = []

    class Spy(ScriptedRouter):
        async def stream(self, messages, tools, sensitive=False, tier="fast", prefer=None):
            if not messages[0].content.startswith("["):
                used.append(prefer)
            async for e in super().stream(messages, tools, sensitive, tier, prefer):
                yield e

    router = Spy([go("a"), say("x"), go("b"), say("x"), go("c"), say("x"), go("d"), say("x")], planner=lambda user: plan_json(("a", "c1"), ("b", "c2")),
                 verifier=lambda user: (False, "no"))
    result = await AgentLoop(router, world.env(), prefer="cheap", strong_prefer="strong").run("open a then b")
    assert result.status == "needs_user" and "strong" in used and used.count("strong") >= 1


async def test_plan_is_saved_and_resume_skips_what_the_world_still_shows():
    world = World()
    world.page, world.seen = "shop", ["shop"]
    saved = []
    plan = Plan("shop then cart", [Checkpoint(1, "open shop", "on page shop", passed=True, verified=True,
                                              claim="Shop open."),
                                   Checkpoint(2, "open cart", "on page cart")])
    router = ScriptedRouter([go("cart"), say("Cart open.")], verifier=lambda user: True)
    result = await AgentLoop(router, world.env(), save=lambda p: saved.append(p.to_json())).run(
        "shop then cart", plan=plan)
    assert result.status == "done" and router.calls["planner"] == 0
    assert router.calls["verifier"] == 2  # boot-up re-check of checkpoint 1, then checkpoint 2
    assert saved and Plan.from_json(saved[-1]).current() is None
    assert "open shop" not in router.executor_inputs[0].split("Now:")[1]


async def test_resume_redoes_a_checkpoint_the_world_no_longer_shows():
    world = World()
    plan = Plan("g", [Checkpoint(1, "open shop", "on page shop", passed=True, verified=True)])
    verdicts = iter([(False, "the shop is not open any more"), (True, "ok")])
    router = ScriptedRouter([go("shop"), say("Shop open.")], verifier=lambda user: next(verdicts))
    result = await AgentLoop(router, world.env()).run("g", plan=plan)
    assert result.status == "done" and world.seen == ["shop"]


def test_single_step_plan_is_one_unfinished_checkpoint():
    plan = single_step_plan("open the news")
    assert len(plan.checkpoints) == 1 and plan.current().goal == "open the news"


async def test_a_captcha_pauses_the_job_for_the_user_and_keeps_the_plan():
    world = World()

    async def call(name, args):
        raise NeedsHuman("I hit a CAPTCHA on shop.example; the window is open for you. Tell me when it is done.")

    env = Environment(system="s", specs=SPECS, call=call, evidence=world.evidence)
    asked = []
    router = ScriptedRouter([go("members")])
    result = await AgentLoop(router, env, ask_user=asked.append).run("open the members area")
    assert result.status == "needs_user" and asked == [result.summary] and "CAPTCHA" in result.question
    assert result.plan.current() is not None and router.calls["verifier"] == 0  # unfinished, so it can be resumed


# -- a CAPTCHA the user clears while Minty waits ------------------------------------------------------------------

async def test_waiting_for_a_person_ends_as_soon_as_the_check_is_gone():
    from minty_core.agentloop import wait_for_person

    looks = iter([True, True, False])
    now = [0.0]

    async def still_blocked():
        return next(looks)

    async def sleep(s):
        now[0] += s

    assert await wait_for_person(still_blocked, 180, every_s=3, sleep=sleep, clock=lambda: now[0]) is True
    assert now[0] == 9.0  # it looks after each pause: three looks, the third finds it gone


async def test_waiting_for_a_person_gives_up_after_the_limit_and_survives_errors():
    from minty_core.agentloop import wait_for_person

    now = [0.0]

    async def broken():
        raise RuntimeError("page gone")

    async def sleep(s):
        now[0] += s

    assert await wait_for_person(broken, 30, every_s=3, sleep=sleep, clock=lambda: now[0]) is False
    assert 30 <= now[0] <= 33
    assert await wait_for_person(broken, 0, sleep=sleep, clock=lambda: now[0]) is False  # 0 = do not wait at all
