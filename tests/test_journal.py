"""The conversation survives a crash and comes back only on a yes (W1-24)."""
import stat

from minty_core.commands import CommandRouter
from minty_core.journal import ConversationJournal, ResumeQuestion
from minty_core.llm.types import Message, ToolCall

CONVERSATION = [
    Message("user", "open the shop"),
    Message("assistant", "", tool_calls=[ToolCall("c1", "browser_open", {"url": "https://shop.example"},
                                                  {"sig": "abc"})]),
    Message("tool", "page text", tool_call_id="c1", name="browser_open"),
    Message("assistant", "It's open."),
    Message("user", "click store"),
    Message("assistant", "Clicked Store."),
]


def test_a_conversation_round_trips_with_its_tool_calls(tmp_path):
    j = ConversationJournal(tmp_path / "conversation.json")
    j.save(CONVERSATION)
    back = ConversationJournal(tmp_path / "conversation.json").pending()
    assert [m.role for m in back] == [m.role for m in CONVERSATION]
    assert back[1].tool_calls[0].arguments == {"url": "https://shop.example"} and back[1].tool_calls[0].extra == {
        "sig": "abc"}
    assert back[2].tool_call_id == "c1" and back[2].name == "browser_open"
    assert stat.S_IMODE((tmp_path / "conversation.json").stat().st_mode) == 0o600


def test_a_clean_exit_leaves_nothing_to_ask(tmp_path):
    j = ConversationJournal(tmp_path / "c.json")
    j.save(CONVERSATION)
    j.mark_clean()
    assert j.pending() is None and ConversationJournal(tmp_path / "missing.json").pending() is None
    j.save([])
    assert j.pending() is None


def test_private_messages_are_never_written_and_the_file_starts_at_a_user_message(tmp_path):
    j = ConversationJournal(tmp_path / "c.json", max_messages=4)
    j.save([Message("user", "secret thing", private=True), *CONVERSATION])
    back = j.pending()
    assert back[0].role == "user" and all("secret" not in m.content for m in back)
    assert (tmp_path / "c.json").read_text().count("secret thing") == 0


class FakeAgent:
    def __init__(self):
        self.history = [Message("user", "new")]


def test_yes_restores_in_front_of_the_new_messages(tmp_path):
    j = ConversationJournal(tmp_path / "c.json")
    j.save(CONVERSATION)
    agent = FakeAgent()
    q = ResumeQuestion(agent, j, j.pending())
    router = CommandRouter(agent=agent, resume=q)
    assert router.handle("yes") == "Okay, continuing where we left off."
    assert [m.content for m in agent.history][-1] == "new" and len(agent.history) == len(CONVERSATION) + 1
    assert not q.pending and router.handle("yes") is None


def test_no_or_anything_else_starts_fresh_and_keeps_the_old_one(tmp_path):
    j = ConversationJournal(tmp_path / "c.json")
    j.save(CONVERSATION)
    q = ResumeQuestion(FakeAgent(), j, j.pending())
    router = CommandRouter(agent=None, resume=q)
    assert router.handle("what time is it") is None  # not a yes: declined, and the request carries on
    assert not q.pending and not (tmp_path / "c.json").exists()
    archived = list((tmp_path / "journal-archive").glob("*.json"))
    assert len(archived) == 1 and "open the shop" in archived[0].read_text()
    j.save(CONVERSATION)
    q2 = ResumeQuestion(FakeAgent(), j, j.pending())
    assert CommandRouter(agent=None, resume=q2).handle("no") == "Okay, starting fresh. The old conversation is kept."


def test_only_five_archives_are_kept(tmp_path):
    import time as _t
    clock = {"now": 1_700_000_000.0}
    j = ConversationJournal(tmp_path / "c.json", clock=lambda: clock["now"])
    for i in range(8):
        clock["now"] += 100
        j.save(CONVERSATION)
        j.archive()
    assert len(list((tmp_path / "journal-archive").glob("*.json"))) == 5
    assert _t is not None
