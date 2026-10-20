"""611: forget a word everywhere, prove it, and say what cannot be reached."""
import json
import sqlite3

import pytest

from minty_core.butler.store import ButlerStore
from minty_core.gists import GistIndex
from minty_core.governance import forget as F
from minty_core.history import ConversationArchive
from minty_core.memory import MemoryStore
from minty_core.receipts import ReceiptStore
from minty_core.research.library import Library
from minty_core.vault.store import Vault

TERM = "Zorblax"


@pytest.fixture
def world(tmp_path):
    memory = MemoryStore(tmp_path / "memory.md")
    memory.add(f"My cousin {TERM} lives in Pune")
    memory.add("Likes mango lassi")
    gists = GistIndex(tmp_path / "memory.db")
    gists.add("fact", f"Planning a trip with {TERM} in March", source_ref="remember")
    gists.add("day", f"Talked about the pi camera. {TERM} came up.", source_ref="days/2026-10-12", summary=f"{TERM}!")
    gists.add("fact", "School bus leaves at 7:15", source_ref="remember")
    archive = ConversationArchive(tmp_path / "history")
    archive.log("user", f"Remind me to call {TERM} tomorrow")
    archive.log("assistant", "Okay, noted.")
    (tmp_path / "history" / "days" / "2026-10-12.md").write_text(f"Called {TERM}.\nStudied physics.\n")
    receipts = ReceiptStore(tmp_path / "r.sqlite")
    receipts.record("butler_add", 1, f"butler add with name call {TERM}", request=f"add call {TERM}")
    receipts.record("clock_set", 1, "set a timer")
    butler = ButlerStore(tmp_path / "b.db")
    butler.add("waiting", f"{TERM}: send the form")
    butler.add("chores", "Plants")
    vault = Vault(tmp_path / "brain", git=False)
    note = vault.create(f"Trip with {TERM}", "train tickets", folder="notes")
    vault.create("Pi camera", "ribbon cable", folder="notes")
    lib = Library(tmp_path / "library")
    lib.save("Trip planning", "quick", f"# Trip\nGo with {TERM}", [], [], "summary", "high")
    lib.save("Pi cameras", "quick", "# Pi\nribbon", [], [], "summary", "high")
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "2026-10-12.jsonl").write_text(json.dumps({"kind": "turn", "text": f"asked about {TERM}"}) + "\n"
                                             + json.dumps({"kind": "turn", "text": "weather"}) + "\n")
    stores = [F.MemoryFile(memory), F.Gists(gists), F.Archive(tmp_path / "history"), F.Receipts(receipts),
              F.Butler(butler), F.Notes(vault), F.Library(tmp_path / "library"),
              F.LineFiles("timing traces", lambda: sorted(traces.glob("*.jsonl")))]
    return {"engine": F.ForgetEngine(stores), "memory": memory, "gists": gists, "archive": archive,
            "butler": butler, "vault": vault, "note": note, "tmp": tmp_path, "receipts": receipts}


def test_preview_counts_each_store_without_changing_anything(world):
    pv = world["engine"].preview(TERM)
    assert set(pv.counts) == {"memory file", "memory index", "conversation history", "action receipts", "butler lists",
                              "notes folder", "research library", "timing traces"}
    assert pv.total >= 10 and "appears" in pv.text()
    assert world["memory"].list()[0].count(TERM) == 1  # nothing was touched


def test_after_forget_no_store_returns_the_word_and_the_proof_says_so(world):
    proof = world["engine"].run(TERM)
    assert proof.clean and proof.remaining == {}
    again = world["engine"].preview(TERM)
    assert again.total == 0 and "nothing" in again.text()
    text = proof.text()
    assert "found 0 left" in text and "online models" in text and "git history" in text


def test_neighbouring_data_is_left_alone(world):
    world["engine"].run(TERM)
    assert world["memory"].list() == ["Likes mango lassi"]
    assert [g.gist for g in world["gists"].search("school bus")] == ["School bus leaves at 7:15"]
    assert [c.name for c in world["butler"].list("chores")] == ["Plants"]
    assert [n.title for n in world["vault"].list("notes")] == ["Pi camera"]
    assert "Studied physics." in (world["tmp"] / "history" / "days" / "2026-10-12.md").read_text()
    assert "weather" in (world["tmp"] / "traces" / "2026-10-12.jsonl").read_text()
    assert (world["tmp"] / "library").exists() and any((world["tmp"] / "library").glob("*/pi-cameras*/note.md"))


def test_the_erased_note_is_gone_from_disk_and_index(world):
    world["engine"].run(TERM)
    assert world["vault"].get(world["note"].id) is None
    assert world["vault"].search_ids("train tickets") == []
    assert not (world["vault"].root / world["note"].path).exists()


def test_the_search_index_forgets_too(world):
    world["engine"].run(TERM)
    assert world["gists"].search(TERM.lower()) == []
    with world["gists"]._lock:
        rows = world["gists"]._db.execute("SELECT count(*) FROM gist_fts WHERE gist_fts MATCH ?", ("zorblax",)).fetchone()
    assert rows[0] == 0


def test_too_short_or_empty_terms_are_refused(world):
    for bad in ("", "  ", "ab", "!!"):
        with pytest.raises(F.ForgetError):
            world["engine"].preview(bad)


def test_a_word_that_matches_a_lot_needs_a_second_yes(world):
    memory = world["memory"]
    for i in range(70):
        memory.add(f"Fact number {i} mentions pizza")
    with pytest.raises(F.ForgetError, match="a lot"):
        world["engine"].run("pizza")
    assert len(memory.list()) == 72
    proof = world["engine"].run("pizza", allow_many=True)
    assert proof.clean and len(memory.list()) == 2


def test_percent_and_underscore_in_a_term_are_not_wildcards(world):
    assert world["engine"].preview("100%").total == 0
    assert world["engine"].preview("z_rblax").total == 0
