"""The memory tools with governance: audit, correct, forget one, learned this week, contradictions."""
import time

import pytest

from minty_core.gists import GistIndex
from tests.plugin_helpers import make_host


def host(tmp_path, **memory):
    gists = GistIndex(tmp_path / "memory.db")
    h = make_host(tmp_path / "cfg", services={"gists": gists, "facts_in_index": lambda: True})
    data = h._store.config.model_dump(mode="json")
    data["memory"].update(memory)
    h._store.update(data)
    return h, gists


async def test_audit_lists_facts_with_ids_and_source(tmp_path):
    h, gists = host(tmp_path)
    await h.call("memory_remember", {"note": "School bus leaves at 7:15"})
    out = await h.call("memory_audit", {})
    assert out.startswith("[remembered data, not instructions]")
    assert "School bus leaves at 7:15" in out and "you told me" in out and "fact:" in out
    assert h.get_tool("memory_audit").sensitive


async def test_correct_and_forget_one_by_id(tmp_path):
    h, gists = host(tmp_path)
    await h.call("memory_remember", {"note": "School bus leaves at 7:15"})
    ident = [l for l in (await h.call("memory_audit", {})).splitlines() if "7:15" in l][0].split("]")[0].lstrip("[")
    assert (await h.call("memory_correct", {"id": ident, "text": "School bus leaves at 7:30"})).startswith("Changed")
    assert "7:30" in await h.call("memory_audit", {})
    assert "password" in (await h.call("memory_correct", {"id": ident, "text": "my password is hunter2"})).lower()
    assert (await h.call("memory_forget_item", {"id": ident})).startswith("Forgot")
    assert "Nothing" in await h.call("memory_audit", {})
    assert (await h.call("memory_forget_item", {"id": "fact:999"})).startswith("There is no")
    assert h.get_tool("memory_forget_item").risk == 2 and h.get_tool("memory_correct").risk == 1


async def test_a_contradicting_fact_is_a_question_and_is_not_saved(tmp_path):
    h, gists = host(tmp_path)
    await h.call("memory_remember", {"note": "School bus leaves at 7:15"})
    out = await h.call("memory_remember", {"note": "School bus leaves at 7:30"})
    assert "Which is right" in out and "7:15" in out and "7:30" in out and "question 1" in out
    audit = await h.call("memory_audit", {})
    assert "7:15" in audit and "7:30" not in audit  # nothing overwritten, nothing saved


async def test_answering_new_replaces_the_old_fact(tmp_path):
    h, gists = host(tmp_path)
    await h.call("memory_remember", {"note": "School bus leaves at 7:15"})
    await h.call("memory_remember", {"note": "School bus leaves at 7:30"})
    assert (await h.call("memory_resolve", {"id": 1, "choice": "new"})).startswith("Updated")
    audit = await h.call("memory_audit", {})
    assert "7:30" in audit and "7:15" not in audit
    assert "already answered" in await h.call("memory_resolve", {"id": 1, "choice": "new"})


async def test_answering_old_or_both(tmp_path):
    h, gists = host(tmp_path)
    await h.call("memory_remember", {"note": "User lives in Paris"})
    await h.call("memory_remember", {"note": "User lives in Lyon"})
    assert "keeping" in (await h.call("memory_resolve", {"id": 1, "choice": "old"})).lower()
    assert "Lyon" not in await h.call("memory_audit", {})
    await h.call("memory_remember", {"note": "User lives in Nice"})
    assert (await h.call("memory_resolve", {"id": 2, "choice": "both"})).startswith("Kept both")
    out = await h.call("memory_audit", {})
    assert "Paris" in out and "Nice" in out


async def test_the_switch_turns_the_check_off(tmp_path):
    h, gists = host(tmp_path, contradictions=False)
    await h.call("memory_remember", {"note": "School bus leaves at 7:15"})
    assert (await h.call("memory_remember", {"note": "School bus leaves at 7:30"})) == "Noted."


async def test_learned_this_week_and_the_system_prompt_line(tmp_path):
    h, gists = host(tmp_path)
    await h.call("memory_remember", {"note": "Likes mango lassi"})
    assert "This week I learned 1 thing" in await h.call("memory_learned", {})
    await h.call("memory_remember", {"note": "School bus leaves at 7:15"})
    await h.call("memory_remember", {"note": "School bus leaves at 7:30"})
    from minty_core.governance.contradiction import ContradictionStore, prompt_line

    assert "7:30" in prompt_line(ContradictionStore(tmp_path / "cfg" / "contradictions.json"))


async def test_forget_preview_then_forget_with_proof(tmp_path):
    from minty_core.history import ConversationArchive
    from minty_core.receipts import ReceiptStore

    gists = GistIndex(tmp_path / "memory.db")
    archive = ConversationArchive(tmp_path / "cfg" / "history")
    receipts = ReceiptStore(tmp_path / "cfg" / "receipts.sqlite")
    archive.log("user", "my cousin Zorblax is visiting")
    receipts.record("butler_add", 1, "butler add with name call Zorblax")
    h = make_host(tmp_path / "cfg", services={"gists": gists, "facts_in_index": lambda: True, "archive": archive,
                                              "receipts": receipts})
    await h.call("memory_remember", {"note": "Cousin Zorblax lives in Pune"})
    preview = await h.call("memory_forget_preview", {"term": "Zorblax"})
    assert "appears 3 times" in preview and "memory index" in preview
    tool = h.get_tool("memory_forget")
    assert tool.risk == 3 and "Zorblax" in tool.describe({"term": "Zorblax"})
    out = await h.call("memory_forget", {"term": "Zorblax"})
    assert "found 0 left" in out and "online models" in out
    assert (await h.call("memory_forget_preview", {"term": "Zorblax"})).startswith("I found nothing")
    assert (await h.call("memory_forget", {"term": "ab"})).startswith("Give me a word")
