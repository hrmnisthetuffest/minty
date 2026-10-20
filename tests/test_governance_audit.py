"""605 memory audit view and 610 'what I learned about you this week'."""
from datetime import datetime, timedelta

import pytest

from minty_core.gists import GistIndex
from minty_core.governance.audit import MemoryAudit
from minty_core.governance.learned import learned_this_week
from minty_core.memory import MemoryStore
from minty_core.vault.kit import VaultProvider
from minty_core.config import Config

NOW = datetime(2026, 10, 15, 12, 0).timestamp()
DAY = 86400


@pytest.fixture
def parts(tmp_path):
    memory = MemoryStore(tmp_path / "memory.md")
    gists = GistIndex(tmp_path / "memory.db", clock=lambda: NOW)
    return memory, gists


def audit_for(parts, vault=None):
    memory, gists = parts
    return MemoryAudit(memory, gists, vault, clock=lambda: NOW)


def test_the_audit_lists_every_fact_with_where_it_came_from_and_when(parts):
    memory, gists = parts
    memory.add("Prefers metric units")
    gists.add("fact", "School bus leaves at 7:15", source_ref="remember", created=NOW - 3 * DAY, importance_=8)
    gists.add("day", "Talked about the pi camera ribbon cable", source_ref="days/2026-10-12",
              created=NOW - 3 * DAY)
    entries = audit_for(parts).entries()
    by_text = {e.text: e for e in entries}
    assert by_text["School bus leaves at 7:15"].source == "you told me"
    assert by_text["School bus leaves at 7:15"].date == "2026-10-12"
    assert by_text["Prefers metric units"].store == "memory file"
    assert "Talked about the pi camera ribbon cable" not in by_text  # summaries only when asked for
    everything = audit_for(parts).entries(kind="all")
    assert any(e.source == "our conversations" for e in everything)


def test_a_query_narrows_the_list(parts):
    memory, gists = parts
    gists.add("fact", "School bus leaves at 7:15", source_ref="remember")
    gists.add("fact", "Likes mango lassi", source_ref="remember")
    assert [e.text for e in audit_for(parts).entries(query="bus")] == ["School bus leaves at 7:15"]


def test_edit_changes_the_text_and_keeps_the_source(parts):
    memory, gists = parts
    gid = gists.add("fact", "School bus leaves at 7:15", source_ref="remember", created=NOW - DAY)
    a = audit_for(parts)
    entry = a.edit(f"fact:{gid}", "School bus leaves at 7:30")
    assert entry.text == "School bus leaves at 7:30" and entry.source == "you told me"
    assert [e.text for e in a.entries()] == ["School bus leaves at 7:30"]
    assert gists.search("7:15") == []  # the old words are gone from the index too
    memory.add("Prefers metric units")
    a.edit("line:0", "Prefers imperial units")
    assert memory.list() == ["Prefers imperial units"]


def test_delete_removes_one_entry_and_only_that_one(parts):
    memory, gists = parts
    g1 = gists.add("fact", "School bus leaves at 7:15", source_ref="remember")
    gists.add("fact", "Likes mango lassi", source_ref="remember")
    memory.add("Prefers metric units")
    a = audit_for(parts)
    assert a.delete(f"fact:{g1}") is True and a.delete(f"fact:{g1}") is False
    assert a.delete("line:0") is True and memory.list() == []
    assert [e.text for e in a.entries()] == ["Likes mango lassi"]


def test_bad_ids_are_refused(parts):
    a = audit_for(parts)
    for bad in ("", "x", "fact:abc", "line:99", "note:nope", "../etc"):
        assert a.delete(bad) is False
        with pytest.raises(ValueError):
            a.edit(bad, "x")


def test_notes_written_by_me_show_up_but_your_own_and_untrusted_ones_do_not(parts, tmp_path):
    cfg = Config()
    cfg.vault.enabled, cfg.vault.path, cfg.vault.git = True, str(tmp_path / "brain"), False
    kit = VaultProvider(config=lambda: cfg).kit()
    mine = kit.vault.create("Something I inferred", "x", folder="notes", origin="agent")
    kit.vault.create("Your own note", "x", folder="notes", origin="user")
    kit.vault.create("A web clip", "x", origin="web")
    texts = [e.text for e in audit_for(parts, VaultProvider(config=lambda: cfg)).entries()]
    assert texts == ["Something I inferred"]
    a = audit_for(parts, VaultProvider(config=lambda: cfg))
    assert a.entries()[0].id == f"note:{mine.id}"
    assert a.delete(f"note:{mine.id}") is True  # archived, not erased: forget-with-proof is the way to erase
    assert kit.vault.get(mine.id).path.startswith("_archive/")


def test_what_i_learned_this_week_lists_new_facts_and_can_be_empty(parts):
    memory, gists = parts
    assert learned_this_week(audit_for(parts), NOW) == "I haven't learned anything new about you this week."
    gists.add("fact", "School bus leaves at 7:15", source_ref="remember", created=NOW - 2 * DAY)
    gists.add("fact", "Old fact from last month", source_ref="remember", created=NOW - 30 * DAY)
    text = learned_this_week(audit_for(parts), NOW)
    assert text.startswith("This week I learned 1 thing about you:")
    assert "School bus leaves at 7:15" in text and "Old fact" not in text
    assert "forget" in text.lower()  # how to drop one
