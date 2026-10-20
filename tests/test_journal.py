"""The conversation is saved after every turn; after a crash it is set aside, never asked about or restored (W1-24)."""
import stat

from minty_core.journal import ConversationJournal
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


def test_an_unclean_exit_is_set_aside_silently_and_a_clean_one_is_left_alone(tmp_path):
    j = ConversationJournal(tmp_path / "c.json")
    j.save(CONVERSATION)
    assert j.set_aside_unclean() is not None and not (tmp_path / "c.json").exists()
    archived = list((tmp_path / "journal-archive").glob("*.json"))
    assert len(archived) == 1 and "open the shop" in archived[0].read_text()
    assert j.set_aside_unclean() is None  # nothing left
    j.save(CONVERSATION)
    j.mark_clean()
    assert j.set_aside_unclean() is None and (tmp_path / "c.json").exists()
