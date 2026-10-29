"""A question goes to Telegram only when it was asked on Telegram, and one asked there is answered there.

Before, every approval a background job waited on was mirrored to the phone, wherever the request came from; a class 3 one then said "this one needs
your yes on the laptop" when the button was pressed. Now each question remembers where the request came from."""
import asyncio

from minty_core.approvals import ApprovalStore
from minty_core.jobs import JobRunner, make_job_store
from minty_core.origin import current_origin, origin_scope
from minty_core.telegram.approvals import ApprovalBridge, Passphrase, button_data
from tests.safety_helpers import build as agent_build
from tests.safety_helpers import call, say
from tests.test_telegram_gateway import OWNER, build


def setup(tmp_path, class3=False):
    gw, fake, store, _ = build(tmp_path)
    if class3:
        data = store.config.model_dump(mode="json")
        data["telegram"]["class3_passphrase"] = True
        store.update(data)
    approvals = ApprovalStore(tmp_path / "approvals.sqlite")
    phrase = Passphrase(tmp_path / "passphrase.json")
    if class3:
        phrase.set("correct horse battery")
    return gw, fake, approvals, ApprovalBridge(gw, approvals, phrase)


def test_a_question_remembers_where_the_request_came_from(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite")
    here = approvals.request("files_delete", {"p": 1}, "Delete?", 2)
    with origin_scope("telegram"):
        phone = approvals.request("files_delete", {"p": 2}, "Delete too?", 2)
        assert current_origin() == "telegram"
    assert current_origin() == "laptop" and approvals.origin_of(here.id) == "laptop" and approvals.origin_of(phone.id) == "telegram"


async def test_only_what_was_asked_on_telegram_is_sent_to_telegram(tmp_path):
    gw, fake, approvals, bridge = setup(tmp_path)
    approvals.request("files_delete", {"p": 1}, "Asked on the laptop, a job waits", 2, job_id=7)
    approvals.request("files_delete", {"p": 2}, "Asked out loud on the laptop", 2)
    with origin_scope("telegram"):
        mine = approvals.request("files_delete", {"p": 3}, "Asked on the phone", 2)
    assert await bridge.offer_new() == 1
    assert "Asked on the phone" in fake.sent[0]["text"] and fake.sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == button_data(mine, True)


async def test_a_job_started_from_telegram_asks_on_telegram_and_one_started_on_the_laptop_does_not(tmp_path):
    gw, fake, approvals, bridge = setup(tmp_path)
    runner = JobRunner(make_job_store(tmp_path, "sqlite"), approvals=approvals)

    async def work(ctx):
        return "done" if await ctx.ask_approval("mission_step", {"n": ctx.job.id}, f"Step of job {ctx.job.id}?", poll_s=0.01) else "no"

    laptop = runner.submit("mission", "from the laptop", work, announce=False)
    with origin_scope("telegram"):
        phone = runner.submit("mission", "from the phone", work, announce=False)
    assert laptop.origin == "laptop" and phone.origin == "telegram"
    while not (laptop.approval_id and phone.approval_id):
        await asyncio.sleep(0.01)
    assert await bridge.offer_new() == 1 and f"Step of job {phone.id}?" in fake.sent[0]["text"]
    for job in (laptop, phone):
        approvals.decide(job.approval_id, True, via="test")
        await runner.wait(job.id)


async def test_a_big_question_from_the_phone_without_a_passphrase_is_said_plainly_with_no_dead_button(tmp_path):
    gw, fake, approvals, bridge = setup(tmp_path)
    with origin_scope("telegram"):
        approvals.request("mcp_call", {"n": 1}, "Trash 40 emails?", 3)
    assert await bridge.offer_new() == 1
    sent = fake.sent[0]
    assert "reply_markup" not in sent and "Trash 40 emails?" in sent["text"]
    assert "laptop" in sent["text"] and "telegram-passphrase" in sent["text"]  # what to do about it, not just a refusal


async def test_a_big_question_from_the_phone_with_a_passphrase_has_buttons(tmp_path):
    gw, fake, approvals, bridge = setup(tmp_path, class3=True)
    with origin_scope("telegram"):
        approvals.request("mcp_call", {"n": 1}, "Trash 40 emails?", 3)
    assert await bridge.offer_new() == 1 and fake.sent[0]["reply_markup"]["inline_keyboard"][0]


async def test_a_message_from_telegram_is_handled_as_asked_on_telegram(tmp_path):
    seen = []

    class Agent:
        history = []

        async def respond(self, text, **kw):
            seen.append(current_origin())
            yield "ok."

    gw, fake, *_ = build(tmp_path, agent=Agent())
    await gw.handle_update(fake.say(OWNER, "trash my promotions"))
    assert seen == ["telegram"] and current_origin() == "laptop"


async def test_a_gated_action_asked_on_telegram_is_answered_on_telegram_not_out_loud(tmp_path):
    asked_phone = []

    async def phone(question):
        asked_phone.append(question)
        return True

    agent, host, asked_here = agent_build(tmp_path, {"files_delete": (3, None)}, [[call("files_delete", {"p": "x"})]])
    agent.phone_answer = phone
    with origin_scope("telegram"):
        await say(agent, "delete x")
    assert asked_phone and asked_here == [] and host.called == [("files_delete", {"p": "x"})]


async def test_when_the_phone_cannot_answer_it_the_laptop_still_asks(tmp_path):
    async def phone(question):
        return None  # a big one with no passphrase set: not for the phone

    agent, host, asked_here = agent_build(tmp_path, {"files_delete": (3, None)}, [[call("files_delete", {"p": "x"})]])
    agent.phone_answer = phone
    with origin_scope("telegram"):
        await say(agent, "delete x")
    assert asked_here and host.called


async def test_a_question_asked_on_the_laptop_is_never_sent_to_the_phone_to_answer(tmp_path):
    async def phone(question):
        raise AssertionError("the phone must not be asked")

    agent, host, asked_here = agent_build(tmp_path, {"files_delete": (3, None)}, [[call("files_delete", {"p": "x"})]])
    agent.phone_answer = phone
    await say(agent, "delete x")
    assert asked_here


async def test_the_app_answers_a_phone_question_from_the_phone_and_leaves_big_ones_without_a_passphrase_to_the_laptop(tmp_path):
    from minty_core.context import build_context

    ctx = build_context(tmp_path)
    answer = ctx.agent.phone_answer
    assert answer is not None
    assert await answer("Nothing open with these words?") is None  # no open question: the laptop asks
    small = ctx.services["approvals"].request("files_delete", {"p": 1}, "Delete the old notes?", 2)
    pending = asyncio.create_task(answer("Delete the old notes?", poll_s=0.01))
    await asyncio.sleep(0.05)
    assert not pending.done()
    ctx.services["approvals"].decide(small.id, True, via="telegram")
    assert await pending is True
    ctx.services["approvals"].request("mcp_call", {"n": 1}, "Trash 40 emails?", 3)
    assert await answer("Trash 40 emails?") is None  # big, and no passphrase is set: it needs the laptop


def test_a_job_started_on_the_laptop_does_not_ping_the_phone_and_one_started_on_telegram_does(tmp_path):
    from datetime import datetime

    from minty_core.events import EventBus
    from minty_core.telegram.push import PushRouter

    gw, fake, *_ = build(tmp_path)
    router = PushRouter(gw, EventBus(), quiet=lambda now: False, state_path=tmp_path / "held.json", clock=lambda: datetime(2026, 10, 29, 12, 0))
    done = {"type": "job", "id": 3, "goal": "tidy the inbox", "status": "done", "progress": "40 moved."}
    assert router.from_event({**done, "origin": "laptop"}) is None
    assert "Job 4" in router.from_event({**done, "id": 4, "origin": "telegram"})["text"]
    # what a job says (needs your approval, finished) follows the same rule; everything else that is told to the phone is unchanged
    assert router.from_event({"type": "notify", "text": "Mission 3 needs your approval", "job_origin": "laptop"}) is None
    assert router.from_event({"type": "notify", "text": "Mission 4 is done", "job_origin": "telegram"}) is not None
    assert router.from_event({"type": "notify", "text": "Your timer is done."}) is not None


async def test_the_apps_job_events_and_notices_carry_where_the_job_was_asked(tmp_path):
    from minty_core.context import build_context

    ctx = build_context(tmp_path)
    bus, jobs = ctx.bus, ctx.services["jobs"]
    events, notices = bus.subscribe({"job"}), bus.subscribe({"notify"})

    async def work(c):
        return "Done. All of it."

    here = jobs.submit("mission", "from the laptop", work)
    with origin_scope("telegram"):
        there = jobs.submit("mission", "from the phone", work)
    await jobs.wait(here.id)
    await jobs.wait(there.id)
    origins = {e["id"]: e["origin"] for e in list(events._queue) if "origin" in e}
    assert origins == {here.id: "laptop", there.id: "telegram"}
    told = {e["job_origin"] for e in list(notices._queue) if "job_origin" in e}
    assert told == {"laptop", "telegram"}
