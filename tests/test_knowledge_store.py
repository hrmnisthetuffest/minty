"""The Second Brain store: originals are kept, provenance is explicit, and what came from outside can never pass for yours or checked (knowledge/store.py)."""
import os
import stat

import pytest

from minty_core.knowledge.store import KnowledgeError, KnowledgeStore


@pytest.fixture
def kb(tmp_path):
    clock = {"t": 1_000_000.0}
    store = KnowledgeStore(tmp_path / "knowledge.sqlite", clock=lambda: clock["t"])
    store.tick = lambda s=86400.0: clock.__setitem__("t", clock["t"] + s)
    return store


def test_an_original_is_kept_exactly_once_and_a_file_original_is_private_to_the_owner(kb):
    data = b"%PDF-1.4 the real bytes"
    a = kb.add_source("pdf", "extracted text", name="paper.pdf", data=data, origin="outside")
    b = kb.add_source("pdf", "extracted text", name="paper again.pdf", data=data, origin="outside")
    assert b["duplicate"] is True and b["id"] == a["id"] and len(kb.sources()) == 1
    assert open(a["path"], "rb").read() == data and stat.S_IMODE(os.stat(a["path"]).st_mode) == 0o600  # the bytes, untouched
    assert kb.add_source("text", "extracted text")["duplicate"] is False  # same words typed by hand are a different original


def test_a_summary_sits_beside_the_content_and_editing_it_never_touches_the_words_or_the_source(kb):
    src = kb.add_source("text", "Long original thought about wholesale margins being better than retail.", origin="you")
    it = kb.add_item("idea", "Wholesale first", "Long original thought about wholesale margins being better than retail.", summary="Wholesale beats retail.", sources=[src["id"]])
    kb.update_item(it["id"], summary="A different gist.", actor="minty")
    got = kb.item(it["id"])
    assert got["body"].startswith("Long original thought") and got["summary"] == "A different gist." and got["authored"] == "you"
    assert kb.source(src["id"])["text"].startswith("Long original thought") and kb.evidence(it["id"])[0]["source"] == src["id"]


def test_content_from_a_turn_that_read_outside_is_outside_and_unverified_whatever_it_claims(kb):
    it = kb.add_item("finding", "Acme grew 40%", "From an article.", authored="you", status="verified", outside=True)
    assert (it["authored"], it["status"]) == ("outside", "unverified")
    with pytest.raises(KnowledgeError, match="read outside"):
        kb.set_status(it["id"], "verified", actor="minty", outside=True)
    own = kb.add_item("idea", "Try a newsletter", "mine", authored="you")
    assert kb.set_status(own["id"], "verified", actor="you")["status"] == "verified"  # the owner can say so


def test_minty_cannot_verify_a_claim_that_has_nothing_behind_it_but_can_once_a_source_is_linked(kb):
    it = kb.add_item("finding", "Battery lasts 12h", "claim", authored="minty")
    with pytest.raises(KnowledgeError, match="Nothing to verify"):
        kb.set_status(it["id"], "verified", actor="minty")
    src = kb.add_source("link", "spec sheet says 12h", uri="https://example.test/spec", origin="outside")
    kb.cite(it["id"], src["id"], quote="12h")
    assert kb.set_status(it["id"], "verified", actor="minty")["status"] == "verified"


def test_changing_the_words_of_a_verified_item_makes_it_unchecked_again_and_the_history_keeps_what_it_was(kb):
    it = kb.add_item("finding", "Rent is 900", "Rent is 900 a month.", authored="you", status="verified")
    kb.tick()
    kb.update_item(it["id"], body="Rent is 950 a month.", actor="minty")
    got = kb.item(it["id"])
    assert got["status"] == "unverified" and got["authored"] == "minty"  # new words are not the ones that were checked, and not only yours
    edit = kb.history(f"item:{it['id']}")[0]
    assert edit["action"] == "edited" and edit["detail"]["body"][0] == "Rent is 900 a month."


def test_people_organisations_projects_and_decisions_are_referenced_not_copied(kb):
    for kind in ("person", "organization", "project", "decision"):
        with pytest.raises(KnowledgeError, match="world model"):
            kb.add_item(kind, "Acme", "x")
    assert kb.add_item("zzz-unknown", "A thing", "x")["kind"] == "note"  # an unknown kind is just a note


def test_links_are_typed_references_that_must_exist_and_the_same_link_is_one_link(kb):
    a, b = kb.add_item("idea", "A", ""), kb.add_item("idea", "B", "")
    link = kb.link(f"item:{a['id']}", "supports", f"item:{b['id']}", why="because")
    again = kb.link(f"item:{a['id']}", "Supports", f"item:{b['id']}")
    assert link["status"] == "confirmed" and again["duplicate"] is True and len(kb.links()) == 1
    kb.link(f"item:{a['id']}", "about", "world:Acme")  # another store's thing is referenced by name
    for bad in (("item:nope", "relates to", f"item:{b['id']}"), (f"item:{a['id']}", "relates to", f"item:{a['id']}"), ("acme", "x", f"item:{b['id']}")):
        with pytest.raises(KnowledgeError):
            kb.link(*bad)


def test_outside_content_can_only_suggest_links_and_cannot_confirm_one(kb):
    a, b = kb.add_item("idea", "A", ""), kb.add_item("idea", "B", "")
    sug = kb.link(f"item:{a['id']}", "supports", f"item:{b['id']}", outside=True)
    assert sug["status"] == "suggested" and kb.links() == []
    with pytest.raises(KnowledgeError, match="outside"):
        kb.set_link(sug["id"], "confirmed", actor="minty", outside=True)
    assert kb.set_link(sug["id"], "confirmed", actor="you")["status"] == "confirmed" and len(kb.links()) == 1


def test_a_suggestion_the_owner_rejected_does_not_come_back_by_itself_but_the_owner_can_make_it(kb):
    a, b = kb.add_item("idea", "A", ""), kb.add_item("idea", "B", "")
    first = kb.link(f"item:{a['id']}", "relates to", f"item:{b['id']}", status="suggested", by="auto", score=0.5)
    kb.set_link(first["id"], "rejected", actor="you")
    assert kb.link(f"item:{a['id']}", "relates to", f"item:{b['id']}", status="suggested", by="auto")["status"] == "rejected"
    assert kb.link(f"item:{a['id']}", "relates to", f"item:{b['id']}", by="minty")["status"] == "rejected"  # nor can the agent
    assert kb.link(f"item:{a['id']}", "relates to", f"item:{b['id']}", by="you")["status"] == "confirmed"


def test_search_finds_by_stem_hides_archived_and_never_deletes(kb):
    it = kb.add_item("lesson", "Negotiating suppliers", "Always ask for volume discounts when negotiating.", tags=["Business"])
    assert [i for i, _ in kb.search_items("negotiate discount")] == [it["id"]]
    kb.archive(it["id"])
    assert kb.search_items("negotiate") == [] and kb.item(it["id"]) is not None and kb.items() == []
    assert kb.search_items("negotiate", include_archived=True)
    kb.archive(it["id"], archived=False)
    assert kb.search_items("business")  # tags are searchable


def test_private_knowledge_is_absent_from_every_default_view(kb):
    kb.add_item("idea", "Secret plan", "my private plan", private=True)
    kb.add_source("text", "my private plan", private=True)
    kb.add_capture("my private plan", private=True)
    assert kb.items() == [] and kb.search_items("plan") == [] and kb.sources() == [] and kb.captures() == []
    assert kb.stats()["items"] == 0 and kb.stats(include_private=True)["items"] == 1
    assert len(kb.items(include_private=True)) == 1 and kb.search_sources("plan") == []


def test_the_inbox_keeps_the_raw_input_and_every_state_it_goes_through(kb):
    c = kb.add_capture("call the bank about the loan", channel="voice")
    assert c["status"] == "pending" and c["raw"] == "call the bank about the loan" and c["origin"] == "you"
    kb.update_capture(c["id"], status="failed", error="boom", attempts=1)
    assert kb.capture(c["id"])["error"] == "boom"
    kb.update_capture(c["id"], status="archived")
    assert kb.captures() == [] and kb.captures("archived")[0]["id"] == c["id"]
    with pytest.raises(KnowledgeError):
        kb.update_capture(c["id"], status="nonsense")
    with pytest.raises(KnowledgeError, match="nothing to capture"):
        kb.add_capture("   ")
    assert kb.delete_capture(c["id"]) is True and kb.capture(c["id"]) is None
    assert any(h["action"] == "deleted" for h in kb.history(f"capture:{c['id']}"))  # the removal itself is remembered


def test_nothing_with_a_wrong_shape_gets_in(kb):
    with pytest.raises(KnowledgeError, match="title"):
        kb.add_item("idea", "   ", "body")
    with pytest.raises(KnowledgeError, match="too long"):
        kb.add_item("idea", "x" * 200, "")
    with pytest.raises(KnowledgeError):
        kb.update_item(kb.add_item("idea", "A", "")["id"], authored="you")  # provenance is not editable
