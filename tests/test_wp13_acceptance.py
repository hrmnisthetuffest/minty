"""WP-13 acceptance (docs/plan-master.md), through the real application wiring."""
import json
import sqlite3
from pathlib import Path

import pytest

from minty_core.context import build_context

TERM = "Zorblax"


@pytest.fixture
def ctx(tmp_path):
    c = build_context(tmp_path / "cfg")
    data = c.store.config.model_dump(mode="json")
    data["vault"].update(enabled=True, path=str(tmp_path / "brain"), git=False, encryption=True)
    data["butler"]["enabled"] = True
    c.store.update(data)
    return c


async def test_after_forget_no_store_returns_the_word(ctx, tmp_path):
    host = ctx.host
    await host.call("memory_remember", {"note": f"Cousin {TERM} lives in Pune"})
    ctx.memory.add(f"Phone for {TERM} is in the blue book")
    ctx.services["archive"].log("user", f"call {TERM} tomorrow")
    ctx.services["archive"].log("assistant", f"I will remind you about {TERM}.")
    ctx.services["receipts"].record("butler_add", 1, f"butler add with name call {TERM}")
    await host.call("butler_add", {"domain": "waiting", "name": f"{TERM}: the form", "when": "friday"})
    kit = ctx.services["vault"].kit()
    kit.vault.create(f"Trip with {TERM}", "train tickets", folder="notes", origin="user")
    kit.vault.create("Pi camera", "ribbon cable", folder="notes", origin="user")
    traces = tmp_path / "cfg" / "traces"
    traces.mkdir(exist_ok=True)
    (traces / "x.jsonl").write_text(json.dumps({"text": f"asked about {TERM}"}) + "\n")

    before = await host.call("memory_forget_preview", {"term": TERM})
    assert "appears" in before and "notes folder" in before and "memory index" in before
    out = await host.call("memory_forget", {"term": TERM})
    assert "found 0 left" in out

    # every way of asking now comes back empty
    assert "Nothing" in await host.call("memory_recall", {"query": TERM})
    assert "Nothing" in await host.call("memory_audit", {"query": TERM, "kind": "all"})
    assert ctx.memory.list() == []
    assert ctx.services["archive"].search_raw(TERM) == []
    assert kit.vault.search_ids(TERM) == [] and kit.vault.search_ids("train tickets") == []
    assert "nothing" in (await host.call("butler_find", {"domain": "waiting", "text": TERM})).lower()
    assert TERM not in (traces / "x.jsonl").read_text()
    db = sqlite3.connect(tmp_path / "cfg" / "memory.db")
    assert db.execute("SELECT count(*) FROM items WHERE gist LIKE ? OR summary LIKE ?",
                      (f"%{TERM}%", f"%{TERM}%")).fetchone()[0] == 0
    assert [n.title for n in kit.vault.list("notes")] == ["Pi camera"]  # neighbours untouched


async def test_an_encrypted_note_is_unreadable_without_the_passphrase_and_absent_from_search_while_locked(ctx, tmp_path):
    kit = ctx.services["vault"].kit()
    kit.private.create("a long enough passphrase")
    kit.private.unlock("a long enough passphrase")
    saved = await ctx.host.call("vault_private_add", {"title": "Worry", "text": "uniqueword about the physics exam"})
    ident = saved.split("(")[1].split(")")[0]
    assert ident in await ctx.host.call("vault_private_find", {"query": "uniqueword"})
    kit.private.lock()
    brain = tmp_path / "brain"
    raw = b"".join(p.read_bytes() for p in brain.rglob("*") if p.is_file() and ".git" not in p.parts)
    assert b"uniqueword" not in raw and b"physics exam" not in raw
    assert kit.vault.search_ids("uniqueword") == []
    assert "locked" in (await ctx.host.call("vault_private_find", {"query": "uniqueword"})).lower()
    assert "locked" in (await ctx.host.call("vault_private_open", {"id": ident})).lower()


async def test_a_contradicting_fact_raises_a_question_and_changes_nothing(ctx):
    await ctx.host.call("memory_remember", {"note": "School bus leaves at 7:15"})
    out = await ctx.host.call("memory_remember", {"note": "School bus leaves at 7:30"})
    assert "Which is right" in out
    audit = await ctx.host.call("memory_audit", {})
    assert "7:15" in audit and "7:30" not in audit
    # the question is in the next prompt until it is answered
    assert "7:30" in ctx.agent._system_prompt("hello")
    await ctx.host.call("memory_resolve", {"id": 1, "choice": "new"})
    assert "7:30" in await ctx.host.call("memory_audit", {})
    assert "Open question" not in ctx.agent._system_prompt("hello")


async def test_key_loss_is_warned_about_before_the_area_is_made(ctx):
    import io

    from minty_core.vault.cli import run

    out = io.StringIO()
    answers = iter(["a long enough passphrase"] * 2)
    run(["private-create"], ctx.services["vault"], out=out, stdin=io.StringIO(""), ask_secret=lambda p: next(answers))
    assert "recovery phrase" in out.getvalue().lower() and "lose the private notes" in out.getvalue()


def test_no_governance_tool_can_be_used_to_create_standing_approvals_or_unlock_private_notes():
    names = [n for p in __import__("pathlib").Path("src/minty_core/plugins/builtin").glob("*/plugin.py")
             for n in __import__("re").findall(r'Tool\("([a-z_]+)"', p.read_text())]
    assert not [n for n in names if "unlock" in n or "passphrase" in n or "grant" in n]
