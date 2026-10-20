"""Telegram chats: the newest 20 messages in full, up to 100 messages ago one line per turn, older than that very short."""
import time

from minty_core.agent import Agent, tier_history
from minty_core.config import ConfigStore, TurnConfig
from minty_core.journal import ConversationJournal
from minty_core.llm.types import Message, TextDelta, ToolCall
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from tests.test_agent import FakeHost, ScriptedRouter


def turns(n, *, chat=True, tool_at=()):
    """n user/assistant pairs: 'question 0' ... 'question n-1', oldest first."""
    out = []
    for i in range(n):
        out.append(Message("user", f"question {i} " + "word " * 30, chat=chat))
        calls = [ToolCall(f"c{i}", "weather_get_weather", {})] if i in tool_at else []
        out.append(Message("assistant", "", calls, chat=chat) if calls else Message("assistant", f"answer {i} " + "text " * 30, chat=chat))
        if calls:
            out.append(Message("tool", "sunny", tool_call_id=f"c{i}", name="weather_get_weather", chat=chat))
            out.append(Message("assistant", f"answer {i} " + "text " * 30, chat=chat))
    return out


def chat_messages(history):
    return [m for m in history if m.role == "user" or (m.role == "assistant" and m.content)]


def test_the_newest_twenty_messages_stay_in_full():
    note, recent = tier_history(turns(150), full=20, compressed=100)
    assert len(chat_messages(recent)) == 20 and recent[0].role == "user"
    assert recent[-1].content.startswith("answer 149") and recent[0].content.startswith("question 140")
    assert "question 140" not in note  # nothing is shown twice


def test_between_twenty_and_a_hundred_messages_ago_each_turn_is_one_line():
    note, _ = tier_history(turns(150), full=20, compressed=100)
    assert 'You asked: "question 120' in note and 'replied: "answer 120' in note  # about 60 messages ago
    assert 'You asked: "question 100' in note  # exactly 100 messages ago is still compressed, not super
    line = next(l for l in note.splitlines() if "question 120" in l)
    assert len(line) < 250  # 61 words of text became one short line


def test_older_than_a_hundred_messages_is_very_short_and_has_no_replies():
    note, _ = tier_history(turns(150, tool_at={95}), full=20, compressed=100)
    old, _, rest = note.partition("Earlier in this conversation")
    assert "Long ago" in old and "question 95" in old and "weather_get_weather" in old
    assert "answer 95" not in old and "replied" not in old  # replies are dropped at this tier
    assert 'You asked: "question 95 ' not in rest  # and it is not listed twice
    assert len(old) <= 1700  # however long the chat, it stays a few lines


def test_a_chat_that_is_still_short_changes_nothing():
    history = turns(8)
    assert tier_history(history, full=20, compressed=100) == ("", history)


def test_a_long_chat_never_splits_a_turn_and_starts_with_a_user_message():
    _, recent = tier_history(turns(60, tool_at={58}), full=20, compressed=100)
    assert recent[0].role == "user"
    assert [m.role for m in recent if m.role == "tool"] == ["tool"] or all(m.role != "tool" for m in recent)


# -- the agent keeps a Telegram chat longer than the 20-message voice window -----------------------------------------

def make(tmp_path, rounds, **kw):
    store = ConfigStore(tmp_path)
    store.load()
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    async def confirm(prompt):
        return True

    router = ScriptedRouter(rounds)
    return Agent(router, FakeHost(), store, personalities, MemoryStore(tmp_path / "memory.md"), confirm, **kw), router


async def test_a_chat_turn_is_kept_beyond_the_voice_window_and_a_voice_turn_is_not(tmp_path):
    agent, _ = make(tmp_path, [[TextDelta(f"r{i}.")] for i in range(30)], max_history=4)
    for i in range(10):
        _ = [s async for s in agent.respond(f"chat {i}", chat=True)]
    assert len(agent.history) == 20 and all(m.chat for m in agent.history)  # nothing was cut at 4 messages
    for i in range(3):
        _ = [s async for s in agent.respond(f"voice {i}")]
    assert [m.chat for m in agent.history[-4:]] == [False] * 4 and sum(not m.chat for m in agent.history) == 4  # voice keeps 4
    assert agent.history[0].content == "chat 0"  # the chat is still there, with the voice turns after it


async def test_chat_history_is_capped(tmp_path):
    agent, _ = make(tmp_path, [[TextDelta("ok.")] for _ in range(40)], max_history=4)
    agent._store.config.turn.chat_keep_messages = 20
    for i in range(15):
        _ = [s async for s in agent.respond(f"chat {i}", chat=True)]
    assert len(agent.history) == 20 and agent.history[0].content == "chat 5"


async def test_chat_turns_outlive_the_thirty_minute_window_but_private_ones_do_not(tmp_path):
    agent, _ = make(tmp_path, [[TextDelta("ok.")] for _ in range(4)])
    old = time.time() - 3 * 3600
    agent.history += [Message("user", "voice earlier", ts=old), Message("assistant", "ok", ts=old),
                      Message("user", "chat earlier", ts=old, chat=True), Message("assistant", "ok", ts=old, chat=True),
                      Message("user", "secret chat", ts=old, chat=True, private=True),
                      Message("assistant", "ok", ts=old, chat=True, private=True)]
    _ = [s async for s in agent.respond("now")]
    texts = [m.content for m in agent.history if m.role == "user"]
    assert "chat earlier" in texts and "now" in texts
    assert "voice earlier" not in texts and "secret chat" not in texts


async def test_chat_turns_expire_after_the_chat_hours(tmp_path):
    agent, _ = make(tmp_path, [[TextDelta("ok.")]])
    agent._store.config.turn.chat_keep_hours = 24
    old = time.time() - 30 * 3600
    agent.history += [Message("user", "last week", ts=old, chat=True), Message("assistant", "ok", ts=old, chat=True)]
    _ = [s async for s in agent.respond("now", chat=True)]
    assert [m.content for m in agent.history if m.role == "user"] == ["now"]


async def test_the_model_sees_the_tiers(tmp_path):
    agent, router = make(tmp_path, [[TextDelta("ok.")]])
    agent.history += turns(150)
    _ = [s async for s in agent.respond("and now?", chat=True)]
    system = router.calls[-1][0].content
    assert "Long ago" in system and "Earlier in this conversation" in system
    sent = [m.content for m in router.calls[-1][1:] if m.role == "user"]
    assert len(sent) == 10 and sent[-1] == "and now?"  # the newest 20 messages: 9 earlier turns in full + this one


def test_the_defaults_are_twenty_a_hundred_and_a_long_keep():
    t = TurnConfig()
    assert (t.chat_full_messages, t.chat_compressed_messages) == (20, 100) and t.chat_keep_messages >= 200


def test_the_journal_remembers_which_messages_were_a_chat(tmp_path):
    journal = ConversationJournal(tmp_path / "j.json")
    journal.save([Message("user", "hi", chat=True), Message("assistant", "yo", chat=True), Message("user", "x"),
                  Message("assistant", "y")])
    restored = journal._read()["messages"]
    assert [m.get("chat") for m in restored] == [True, True, False, False]
