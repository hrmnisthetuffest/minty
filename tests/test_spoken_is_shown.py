"""Whatever Minty says out loud is also in the chat text, so nothing is spoken that the screen does not show
(the filler while it works, "I couldn't make that out", a question it asks, a line the panel has it say)."""
import asyncio
import contextlib

from minty_core.pipeline import ONE_MOMENT, UNCLEAR
from tests.test_pipeline import build, finish, wait_until
from tests.test_turn_deadline import DelayedAgent, set_turn


def shown(events):
    return [e["text"] for e in list(events._queue) if e.get("role") == "assistant"]


async def test_the_filler_said_while_it_works_is_in_the_chat_too(tmp_path):
    p, f = build(tmp_path, stt_texts=["slow one"])
    await p.warm_up()
    p.agent = f.agent = DelayedAgent([[(0.3, "Done.")]])
    set_turn(p, notice_after_s=0.1)
    events = f.bus.subscribe({"transcript"})
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Done." in f.player.played)
    await finish(p, f, task)
    assert ONE_MOMENT.encode() in f.player.played and ONE_MOMENT in shown(events)


async def test_not_making_out_what_you_said_is_in_the_chat_too(tmp_path):
    p, f = build(tmp_path, stt_texts=[])  # nothing to hear: recognition fails
    events = f.bus.subscribe({"transcript"})
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: UNCLEAR.encode() in f.player.played)
    await finish(p, f, task)
    assert UNCLEAR in shown(events)


async def test_a_line_the_panel_has_it_say_is_in_the_chat_too(tmp_path):
    p, f = build(tmp_path)
    events = f.bus.subscribe({"transcript"})
    await p.say("Your timer is done.")
    assert b"Your timer is done." in f.player.played and shown(events) == ["Your timer is done."]


async def test_a_question_it_asks_out_loud_is_in_the_chat_too(tmp_path):
    p, f = build(tmp_path)
    events = f.bus.subscribe({"transcript"})
    task = asyncio.create_task(p.confirm("Should I delete the old backups?"))
    await wait_until(lambda: b"Should I delete the old backups?" in f.player.played)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    assert shown(events) == ["Should I delete the old backups?"]
