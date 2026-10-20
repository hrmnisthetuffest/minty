"""Typing indicator, one growing message, "working on it", and nothing if it turns private (W3-05)."""
import asyncio

from tests.telegram_fake import TOKEN, FakeTelegram
from tests.test_telegram_gateway import OWNER
from minty_core.config import ConfigStore
from minty_core.llm.types import Message
from minty_core.telegram.api import BotAPI
from minty_core.telegram.gateway import Sender, TelegramGateway


class SlowAgent:
    def __init__(self, pieces):
        self.pieces, self.history = pieces, []

    async def respond(self, text, **kw):
        self.history.append(Message("user", text))
        for delay, piece, *flags in self.pieces:
            await asyncio.sleep(delay)
            if flags and flags[0] == "private":
                self.history.append(Message("tool", "secret", private=True))
            yield piece


def build(tmp_path, agent, **kw):
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["telegram"]["allowed_ids"] = [OWNER]
    store.update(data)
    fake = FakeTelegram()
    bot = BotAPI(TOKEN, fake.client)
    gw = TelegramGateway(bot, store, agent, machine="laptop", sender=Sender(bot, interval=0.0),
                         progress_after_s=kw.get("progress_after_s", 8.0), typing_every_s=0.02)
    return gw, fake


async def test_the_answer_grows_in_one_message_instead_of_many(tmp_path):
    gw, fake = build(tmp_path, SlowAgent([(0.0, "First."), (0.0, "Second."), (0.0, "Third.")]))
    await gw.handle_update(fake.say(OWNER, "tell me three things"))
    assert len(fake.sent) == 1 and fake.sent[0]["text"] == "[laptop] First."
    assert fake.edits[-1]["text"] == "[laptop] First. Second. Third."
    assert fake.actions and fake.actions[0]["action"] == "typing"


async def test_a_slow_request_shows_working_on_it_then_the_answer_in_the_same_message(tmp_path):
    gw, fake = build(tmp_path, SlowAgent([(0.15, "Done, sir.")]), progress_after_s=0.05)
    await gw.handle_update(fake.say(OWNER, "look into it"))
    assert fake.sent[0]["text"] == "[laptop] Working on it..." and fake.sent[0].get("disable_notification") is True
    assert fake.edits[-1]["text"] == "[laptop] Done, sir." and len(fake.sent) == 1


async def test_streaming_can_be_off_and_typing_can_be_off(tmp_path):
    gw, fake = build(tmp_path, SlowAgent([(0.0, "One."), (0.0, "Two.")]))
    data = gw.store.config.model_dump(mode="json")
    data["telegram"].update(stream=False, typing=False)
    gw.store.update(data)
    await gw.handle_update(fake.say(OWNER, "go"))
    assert fake.texts(OWNER) == ["[laptop] One. Two."] and fake.edits == [] and fake.actions == []


async def test_an_answer_that_turns_private_midway_is_replaced_and_the_rest_is_never_sent(tmp_path):
    gw, fake = build(tmp_path, SlowAgent([(0.0, "Let me look."), (0.0, "Your heart rate is 61.", "private"),
                                          (0.0, "Resting.")]))
    await gw.handle_update(fake.say(OWNER, "how is my heart"))
    everything = " ".join(fake.texts(OWNER) + [e["text"] for e in fake.edits])
    assert "61" not in everything and "Resting" not in everything
    assert fake.edits[-1]["text"] == "[laptop] That answer is private, so I kept it for the laptop."


async def test_a_model_failure_is_a_plain_message(tmp_path):
    class Broken:
        history = []

        async def respond(self, text, **kw):
            raise RuntimeError("all providers failed")
            yield  # noqa

    gw, fake = build(tmp_path, Broken())
    await gw.handle_update(fake.say(OWNER, "hi"))
    assert fake.texts(OWNER) == ["[laptop] Sorry, I couldn't get an answer just now."]
