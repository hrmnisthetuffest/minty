"""WP-23 acceptance: the coach through the real context, agent gate, hub, importers and Telegram delivery."""
import asyncio
import json

import httpx
import pytest

from tests.test_coach_safety import AGE_PROMPTS, ORDINARY, RED_FLAGS
from tests.telegram_fake import FakeTelegram
from tests.test_telegram_gateway import OWNER
from minty_core.coach import engine
from minty_core.coach.briefs import Coach
from minty_core.coach.commands import CoachCommands
from minty_core.commands import CommandRouter
from minty_core.context import build_context
from minty_core.health import HealthHub
from minty_core.hevy import HevyPoller

DAY = 86400.0
NOW = 1_800_000_000.0
BENCH = "Bench Press (Barbell)"


def history(h, k):
    w = float(round((40 + k * 3.5) / 2.5) * 2.5)
    r = 3 + (k % 6)
    plan = [(w, r), (w, r + 1), (w + 2.5, r), (w + 2.5, r + 1)]
    for i, (wt, reps) in enumerate(plan):
        h.add_workout("hevy", f"f{k}-{i}", NOW - (3 - i) * 7 * DAY, "Push", 3600,
                      [{"exercise": BENCH, "idx": j, "weight_kg": wt, "reps": reps} for j in range(3)])
    return w, r, plan


@pytest.mark.parametrize("k", range(30))
def test_thirty_fixture_histories_match_hand_worked_values(tmp_path, k):
    h = HealthHub(tmp_path / "h.db", clock=lambda: NOW)
    h.grant("training")
    w, r, plan = history(h, k)
    sessions = engine.load_sessions(h)
    e = [wt * (1 + reps / 30) for wt, reps in plan]  # Epley, worked per session
    assert engine.best_e1rm(sessions, BENCH) == pytest.approx(max(e))
    prs = engine.prs_in(sessions[-1], sessions[:-1])
    assert len(prs) == 1 and prs[0].e1rm == pytest.approx(e[3]) and prs[0].previous == pytest.approx(max(e[:3]))
    assert engine.hard_sets_by_muscle(sessions, NOW, 7) == {"chest": 3}  # only the newest session is inside a week
    top, last_reps = w + 2.5, r + 1
    want = (top + 2.5, 5) if last_reps >= 8 else (top, last_reps + 1)
    t = engine.next_targets(sessions, [BENCH])[0]
    assert (t.weight_kg, t.reps) == want


def test_a_few_values_worked_on_paper():
    assert engine.epley(100, 5) == pytest.approx(116.667, abs=1e-3)
    assert engine.epley(60, 10) == pytest.approx(80.0)
    assert engine.epley(82.5, 3) == pytest.approx(90.75)
    assert engine.brzycki(100, 10) == pytest.approx(133.33, abs=0.01)


@pytest.fixture
def ctx(tmp_path):
    c = build_context(tmp_path)
    data = c.store.config.model_dump(mode="json")
    data["coach"].update(enabled=True, age_rules=True, briefs=True)
    c.store.update(data)
    c.services["health"].grant("training")
    c.services["health"].set_profile("age", "15")

    async def boom(*a, **kw):  # a gated request must never reach a model
        raise AssertionError("the model was called")
        yield None

    c.router.stream = boom
    return c


@pytest.mark.parametrize("prompt", AGE_PROMPTS)
async def test_weight_loss_requests_from_a_teenager_are_declined_with_the_rules_on(ctx, prompt):
    reply = "".join([s async for s in ctx.agent.respond(prompt)])
    assert "strength" in reply.lower()


@pytest.mark.parametrize("prompt", RED_FLAGS)
async def test_a_red_flag_stops_coaching_in_one_turn_and_never_makes_a_plan(ctx, prompt):
    reply = "".join([s async for s in ctx.agent.respond(prompt)])
    assert any(w in reply.lower() for w in ("doctor", "adult", "someone", "tele-manas"))
    assert "plan" not in reply.lower() and "sets" not in reply.lower()
    held = "".join([s async for s in ctx.agent.respond("ok so give me a workout plan for tomorrow")])
    assert "wait" in held.lower()


async def test_with_the_age_rules_off_the_same_request_goes_to_the_model_but_floors_still_hold(ctx):
    data = ctx.store.config.model_dump(mode="json")
    data["coach"]["age_rules"] = False
    ctx.store.update(data)
    with pytest.raises(AssertionError, match="model was called"):
        [s async for s in ctx.agent.respond("help me lose weight")]  # allowed: it reaches the (fake) model
    reply = "".join([s async for s in ctx.agent.respond("I want to eat 900 calories a day")])
    assert "won't set a target that extreme" in reply


async def test_every_piece_of_advice_can_be_traced_to_its_numbers(tmp_path):
    from tests.plugin_helpers import make_host

    h = HealthHub(tmp_path / "h.db", clock=lambda: NOW)
    h.grant("training")
    history(h, 4)
    host = make_host(tmp_path / "cfg", services={"health": h})
    data = host._store.config.model_dump(mode="json")
    data["coach"]["enabled"] = True
    host._store.update(data)
    advice = await host.call("coach_week", {})
    assert "hard sets" in advice.lower()

    class Dummy:
        history: list = []

    router = CommandRouter(Dummy(), coach=CoachCommands(h))
    why = router.handle("why did you say that")
    assert "10-20 hard sets per muscle per week" in why and "Last 7 days" in why


async def test_a_synced_workout_gets_a_debrief_and_updates_the_next_brief_and_nothing_comes_before_it(tmp_path):
    from datetime import datetime, timezone

    from minty_core.events import EventBus
    from minty_core.telegram.coach import CoachDelivery
    from minty_core.telegram.push import PushRouter
    from tests.test_telegram_gateway import build

    ended = datetime(2027, 1, 15, 7, 30, tzinfo=timezone.utc).timestamp()
    now = ended + 600
    h = HealthHub(tmp_path / "h.db", clock=lambda: now)
    h.grant("training")
    for i in range(3):  # three earlier Push days, 80 kg x 6 on every set
        h.add_workout("hevy", f"e{i}", ended - (30 - i * 7) * DAY, "Push", 3600,
                      [{"exercise": BENCH, "idx": j, "weight_kg": 80, "reps": 6} for j in range(3)])
    coach = Coach(h, clock=lambda: now)
    assert coach.pending_debriefs() == []  # the session is not in the hub yet: nothing is said, and the old
    #                                        history is not a flood of debriefs

    new = {"id": "hevy-new", "title": "Push", "start_time": "2027-01-15T06:30:00+00:00",
           "end_time": "2027-01-15T07:30:00+00:00",
           "exercises": [{"title": BENCH, "sets": [{"index": i, "type": "normal", "weight_kg": 90, "reps": 6}
                                                   for i in range(3)]}]}
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"page": 1, "page_count": 1, "workouts": [new]})

    poller = HevyPoller(h, lambda: "k" * 36, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await poller.sync_once()).added == 1  # the workout has finished and arrives
    debriefs = coach.pending_debriefs()
    assert len(debriefs) == 1
    d = debriefs[0]
    assert "PR" in d.text and BENCH in d.text and "108.0" in d.text  # 90 x 6: 90 * (1 + 6/30)
    assert "chest 3 hard sets today" in d.text
    assert "90 kg x 7" in Coach(h, clock=lambda: now).pre_workout().text  # the new session moved the target

    gw, fake, store, _ = build(tmp_path / "tg")
    cfg = store.config.model_dump(mode="json")
    cfg["coach"].update(enabled=True, briefs=True)
    store.update(cfg)
    push = PushRouter(gw, EventBus(), quiet=lambda t: False, state_path=tmp_path / "held.json",
                      clock=lambda: datetime(2027, 1, 15, 12, 0))
    delivery = CoachDelivery(gw, push, lambda: h)
    assert fake.sent == []  # nothing reached the phone before the debrief
    event = {"type": "coach", "kind": "debrief", "text": d.text, "workout_id": d.workout_id}
    assert await delivery.handle(event) == "sent"
    assert len(fake.sent) == 1 and fake.sent[0]["reply_markup"]["inline_keyboard"]
