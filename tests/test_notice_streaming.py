"""No "One moment." once the model has started answering (speed fix S-3)."""
import asyncio

from tests.fakes import FakeAgent
from tests.test_pipeline import build, finish, wait_until
from tests.test_turn_deadline import DelayedAgent, set_turn
from minty_core.pipeline import ONE_MOMENT


class StreamingAgent(DelayedAgent):
    """The model's first words arrive at `start`; the first complete sentence a little later."""

    reply_started = False

    def __init__(self, replies, start):
        super().__init__(replies)
        self.start = start

    async def respond(self, text, *, sensitive=False):
        self.heard.append(text)
        self.reply_started = False
        await asyncio.sleep(self.start)
        self.reply_started = True
        for delay, sentence in self.replies.pop(0):
            await asyncio.sleep(delay)
            yield sentence


async def test_no_notice_when_the_model_is_already_answering(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"])
    p.agent = f.agent = StreamingAgent([[(0.35, "Noon.")]], start=0.05)
    set_turn(p, notice_after_s=0.15, notice_skips_streaming=True)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Noon." in f.player.played)
    await finish(p, f, task)
    assert f.player.played == [b"Noon."]


async def test_a_silent_wait_still_gets_the_notice(tmp_path):
    p, f = build(tmp_path, stt_texts=["open chrome"])
    p.agent = f.agent = StreamingAgent([[(0.1, "Chrome is open.")]], start=0.5)
    set_turn(p, notice_after_s=0.15, notice_skips_streaming=True)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Chrome is open." in f.player.played)
    await finish(p, f, task)
    assert f.player.played == [ONE_MOMENT.encode(), b"Chrome is open."]


async def test_the_old_behaviour_is_one_setting_away(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"])
    p.agent = f.agent = StreamingAgent([[(0.35, "Noon.")]], start=0.05)
    set_turn(p, notice_after_s=0.15, notice_skips_streaming=False)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Noon." in f.player.played)
    await finish(p, f, task)
    assert f.player.played == [ONE_MOMENT.encode(), b"Noon."]


def test_fake_agent_is_not_required_to_know_about_streaming():
    assert not hasattr(FakeAgent, "reply_started")  # the pipeline must cope with agents that don't say
