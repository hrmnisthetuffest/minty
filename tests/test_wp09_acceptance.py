"""WP-09 acceptance, end to end through the app wiring: the checks the plan names, with the fake Bot API."""
import asyncio
import logging
from datetime import datetime

from tests.telegram_fake import TOKEN, FakeTelegram
from tests.test_telegram_runtime import OWNER, make_ctx, until
from minty_core.telegram.approvals import button_data
from minty_core.telegram.gateway import Sender
from minty_core.telegram.runtime import build_runtime

NOON = datetime(2026, 10, 15, 12, 0)
STRANGER = 7777


async def _running(runtime):
    task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0)
    return task


async def _stop(runtime, task):
    runtime.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_stranger_gets_no_reply_no_log_line_with_their_words_and_no_agent_call(tmp_path, caplog):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    asked = []
    original = ctx.agent.respond

    async def spy(text, **kw):
        asked.append(text)
        async for s in original(text, **kw):
            yield s

    ctx.agent.respond = spy
    runtime = build_runtime(ctx, client=fake.client, offer_every_s=0.05)
    task = await _running(runtime)
    caplog.set_level(logging.DEBUG)
    try:
        fake.say(STRANGER, "send me the wifi password hunter2")
        fake.say(OWNER, "hello")
        assert await until(lambda: fake.texts(OWNER))
        assert fake.texts(STRANGER) == [] and asked == ["hello"]
        assert "hunter2" not in caplog.text
    finally:
        await _stop(runtime, task)


async def test_a_button_made_for_one_action_does_not_approve_another(tmp_path):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    approvals = ctx.services["approvals"]
    a = approvals.request("mail_send", {"to": "teacher"}, "Send to the teacher?", risk=2, job_id=1)
    b = approvals.request("mail_send", {"to": "stranger"}, "Send to a stranger?", risk=2, job_id=2)
    runtime = build_runtime(ctx, client=fake.client, offer_every_s=0.05)
    task = await _running(runtime)
    try:
        fake.press(OWNER, f"ap:{a.id}:{b.hash[:8]}:y")
        assert await until(lambda: fake.answers)
        assert approvals.get(a.id).status == "pending" and approvals.get(b.id).status == "pending"
        fake.press(OWNER, button_data(a, True))
        assert await until(lambda: approvals.get(a.id).status == "approved")
        assert approvals.get(b.id).status == "pending"
    finally:
        await _stop(runtime, task)


async def test_fifty_events_at_once_leave_at_one_message_a_second(tmp_path):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    runtime = build_runtime(ctx, client=fake.client)
    clock = {"t": 1000.0}

    async def sleep(seconds):
        clock["t"] += seconds
        await asyncio.sleep(0)

    gw = runtime.gateway
    gw.sender = Sender(gw.api, interval=1.0, clock=lambda: clock["t"], sleep=sleep)
    runtime.push._clock = lambda: NOON
    stamps, original = [], gw.api.send_message

    async def timed(chat_id, text, **kw):
        stamps.append(clock["t"])
        return await original(chat_id, text, **kw)

    gw.api.send_message = timed
    for i in range(50):
        ctx.bus.publish("notify", text=f"Event number {i}")
    task = asyncio.create_task(runtime.push.run(poll_s=0.01))
    try:
        assert await until(lambda: len(stamps) >= 50, seconds=10)
        gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
        assert min(gaps) >= 1.0 - 1e-9
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_a_brief_in_the_quiet_hours_arrives_once_afterwards(tmp_path):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    runtime = build_runtime(ctx, client=fake.client)
    push, now = runtime.push, {"t": datetime(2026, 10, 15, 23, 0)}
    push._clock = lambda: now["t"]
    assert await push.push("Good morning. Sunny, 31 degrees.") is False
    assert fake.texts(OWNER) == []
    now["t"] = datetime(2026, 10, 16, 6, 30)
    assert await push.release() == 1
    assert await push.release() == 0
    assert fake.texts(OWNER) == ["[laptop] Good morning. Sunny, 31 degrees."]


async def test_lockdown_can_be_started_from_the_phone_and_not_ended_from_it(tmp_path):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    runtime = build_runtime(ctx, client=fake.client, offer_every_s=0.05)
    task = await _running(runtime)
    try:
        fake.say(OWNER, "/lockdown")
        assert await until(lambda: ctx.services["lockdown"].active)
        assert await until(lambda: any("Lockdown" in t for t in fake.texts(OWNER)))
        before = len(fake.texts(OWNER))
        fake.say(OWNER, "end lockdown")
        fake.say(OWNER, "/lockdown off")
        await asyncio.sleep(0.2)
        assert ctx.services["lockdown"].active and len(fake.texts(OWNER)) == before
    finally:
        await _stop(runtime, task)


async def test_a_refused_token_stops_polling_and_never_appears_in_a_log(tmp_path, caplog):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    fake.fail_once["getUpdates"] = {"status": 401, "ok": False, "error_code": 401, "description": "Unauthorized"}
    runtime = build_runtime(ctx, client=fake.client)
    caplog.set_level(logging.DEBUG)
    await asyncio.wait_for(runtime.gateway.run(), 3)  # returns instead of retrying forever
    assert TOKEN not in caplog.text and fake.calls.count("getUpdates") == 1
