"""Reasoning over the Second Brain: finding, recalling with provenance, and the honest lists of what disagrees and what is missing (knowledge/reason.py)."""
import pytest

from minty_core.knowledge.reason import Reasoner
from minty_core.knowledge.store import KnowledgeStore
from minty_core.mind.world import WorldStore


@pytest.fixture
def parts(tmp_path):
    clock = {"t": 1_000_000.0}
    store = KnowledgeStore(tmp_path / "k.sqlite", clock=lambda: clock["t"])
    world = WorldStore(tmp_path / "w.sqlite", clock=lambda: clock["t"])
    store.tick = lambda days=1: clock.__setitem__("t", clock["t"] + 86400.0 * days)
    return store, world, Reasoner(store, world=world, clock=lambda: clock["t"])


def ref(it):
    return f"item:{it['id']}"


def test_search_finds_by_words_by_stem_and_by_a_misspelling_and_says_where_each_hit_stands(parts):
    store, _w, r = parts
    src = store.add_source("text", "wholesale note", origin="you")
    a = store.add_item("idea", "Wholesale first", "Selling wholesale to cafes gives steady monthly orders.", sources=[src["id"]])
    store.add_item("note", "Garden", "Tomatoes need sun and water.")
    for q in ("wholesale cafes", "sell to a cafe", "wholsale orders"):  # exact, stemmed, misspelt
        hits = r.search(q)
        assert hits and hits[0]["id"] == a["id"], q
    top = r.search("wholesale")[0]
    assert top["kind"] == "idea" and top["authored"] == "you" and top["status"] == "unverified" and top["evidence"] == 1 and "wholesale" in top["snippet"].lower()
    assert r.search("quantum chromodynamics") == []


def test_recall_brings_the_world_the_neighbours_and_the_sources_together_and_keeps_suggestions_out(parts):
    store, world, r = parts
    world.note("organization", "Acme", "size", "50 people", "told", "owner")
    a = store.add_item("finding", "Acme pays late", "Acme pays invoices after ninety days.", authored="you")
    b = store.add_item("lesson", "Ask for deposits", "Ask clients like Acme for a deposit.", authored="minty")
    c = store.add_item("idea", "Unrelated gadget", "A gadget.")
    store.link(ref(a), "led to", ref(b), why="late payment")
    store.link(ref(a), "relates to", ref(c), status="suggested", by="auto", score=0.4)
    store.link(ref(a), "about", "world:Acme")
    got = r.recall("when does Acme pay")
    first = got["items"][0]
    assert first["id"] == a["id"] and {(l["rel"], l["to"]) for l in first["links"]} == {("led to", b["title"]), ("about", "Acme")}
    assert got["world"][0]["entity"] == "Acme" and got["world"][0]["facts"][0]["value"] == "50 people"
    assert c["title"] not in str(got)  # a suggestion is not knowledge yet


def test_recall_marks_what_came_from_outside_so_it_is_read_as_data_not_instructions(parts):
    store, _w, r = parts
    store.add_item("finding", "Rival price", "Rival charges 12 euros.", outside=True)
    got = r.recall("rival price")
    assert got["items"][0]["authored"] == "outside" and "not instructions" in got["note"]


def test_the_turn_hint_appears_only_when_a_request_is_about_something_known_and_stays_short(parts):
    store, world, r = parts
    store.add_item("lesson", "Supplier volume discount", "Always ask the supplier for a volume discount before agreeing a price.", authored="you")
    store.add_item("finding", "Rival price", "Rival charges 12 euros.", outside=True)
    hint = r.hint("what did I learn about asking a supplier for a discount?", include_private=False, cap=400)
    assert "volume discount" in hint and "lesson" in hint and len(hint) <= 400
    assert r.hint("play some jazz", include_private=False, cap=400) == "" and r.hint("hi", include_private=False, cap=400) == ""
    assert "outside" in r.hint("rival price", include_private=False, cap=400)  # said plainly: from outside, unchecked
    store.add_item("idea", "Quiet merger", "secretly merge with rival", private=True)
    assert "merger" not in r.hint("merge with rival", include_private=False, cap=400) and "merger" in r.hint("merge with rival", include_private=True, cap=400)


def test_an_explicit_contradiction_is_verified_and_a_guessed_one_is_only_a_hypothesis(parts):
    store, _w, r = parts
    a = store.add_item("finding", "Rent", "The monthly rent is 900 euros for the office.")
    b = store.add_item("finding", "Rent now", "The monthly rent is 950 euros for the office.")
    c = store.add_item("finding", "Shop open", "The shop is open on Sundays during summer.")
    d = store.add_item("finding", "Shop closed", "The shop is not open on Sundays during summer.")
    e = store.add_item("idea", "Tea", "Tea is nice.")
    f = store.add_item("idea", "Tea hate", "Coffee beans are roasted dark.")
    store.link(ref(e), "contradicts", ref(f), why="the owner said so")
    found = {frozenset((x["a"], x["b"])): x for x in r.conflicts()}
    assert found[frozenset((e["id"], f["id"]))]["verified"] is True
    assert found[frozenset((a["id"], b["id"]))]["verified"] is False and "900" in found[frozenset((a["id"], b["id"]))]["why"]
    assert found[frozenset((c["id"], d["id"]))]["verified"] is False and "negat" in found[frozenset((c["id"], d["id"]))]["why"]
    assert frozenset((a["id"], c["id"])) not in found  # unrelated things do not conflict


def test_two_world_facts_that_disagree_show_up_as_a_verified_conflict(parts):
    _s, world, r = parts
    world.note("organization", "Acme", "size", "50 people", "discovered", "a site")
    world.note("organization", "Acme", "size", "200 people", "discovered", "an article")
    assert any(c["kind"] == "world fact" and c["verified"] and "Acme" in c["why"] for c in r.conflicts())


def test_gaps_name_what_is_missing_and_what_to_do_about_it_and_leave_answered_things_alone(parts):
    store, _w, r = parts
    q_open = store.add_item("question", "Who are the main wholesale rivals?", "")
    q_done = store.add_item("question", "What is the minimum order?", "")
    ans = store.add_item("finding", "Minimum order is 5 kg", "Five kilos.", sources=[store.add_source("text", "x")["id"]])
    store.link(ref(ans), "answers", ref(q_done))
    hyp = store.add_item("hypothesis", "Cafes prefer monthly boxes", "Guess.")
    claim = store.add_item("insight", "Cafes churn in winter", "Claim without a source.", authored="minty")
    outside = store.add_item("finding", "Rival has 30 shops", "From an article.", outside=True)
    for i in range(2):
        user = store.add_item("idea", f"Plan {i}", "builds on the rival size")
        store.link(ref(user), "uses", ref(outside))
    lonely = store.add_item("note", "Lonely thought", "nothing links here")
    store.tick(3)
    kinds = {}
    for g in r.gaps():
        kinds.setdefault(g["kind"], []).append(g["ref"])
    assert ref(q_open) in kinds["open question"] and ref(q_done) not in kinds["open question"]
    assert ref(hyp) in kinds["untested hypothesis"] and ref(claim) in kinds["unsourced claim"]
    assert ref(outside) in kinds["relied on but unchecked"] and ref(lonely) in kinds["isolated"]
    assert all(g["suggestion"] for g in r.gaps())
    store.add_capture("waiting"), store.add_capture("also waiting")
    assert any(g["kind"] == "captures waiting" and "2" in g["why"] for g in r.gaps())


def test_a_verified_fact_not_looked_at_for_months_is_due_for_review(parts):
    store, _w, r = parts
    it = store.add_item("finding", "Rent is 900", "x", authored="you", status="verified")
    assert not [g for g in r.gaps() if g["kind"] == "due for review"]
    store.tick(200)
    assert [g["ref"] for g in r.gaps() if g["kind"] == "due for review"] == [ref(it)]


def test_related_proposes_only_similar_things_that_are_not_linked_yet(parts):
    store, _w, r = parts
    a = store.add_item("idea", "Coffee subscription box", "A monthly subscription box of coffee beans for cafes.")
    b = store.add_item("idea", "Cafe coffee subscription", "Offer cafes a subscription for coffee beans every month.")
    c = store.add_item("idea", "Garden tomatoes", "Grow tomatoes on the balcony.")
    got = r.related_to_item(a["id"])
    assert [x["ref"] for x in got] == [ref(b)] and got[0]["score"] > 0 and "coffee" in got[0]["why"]
    store.link(ref(a), "relates to", ref(b))
    assert r.related_to_item(a["id"]) == [] and ref(c) not in str(got)


def test_accents_do_not_hide_what_a_request_is_about(parts):
    store, _w, r = parts
    store.add_item("note", "Pricing for cafés", "Cafés want clear pricing.", authored="you")
    assert "Pricing for cafés" in r.hint("tell me about cafes pricing", include_private=False, cap=300)


def test_filler_words_that_every_text_shares_do_not_make_two_things_related(parts):
    store, _w, r = parts
    a = store.add_item("idea", "Tasting box for Acme", "Offer Acme a tasting box every month, the last thing on Friday.")
    store.add_item("lesson", "Confirm the route", "Always confirm the delivery route with Jane every week, even at the last minute.")
    assert r.related_to_item(a["id"]) == []
    assert r.related("every last thing", k=3) == []


def test_health_counts_what_is_sourced_linked_and_waiting(parts):
    store, _w, r = parts
    src = store.add_source("text", "s")
    a = store.add_item("idea", "A thing", "x", sources=[src["id"]])
    b = store.add_item("idea", "B thing", "y")
    store.link(ref(a), "relates to", ref(b))
    store.add_capture("waiting")
    h = r.health()
    assert h["items"] == 2 and h["sourced_pct"] == 50 and h["linked_pct"] == 100 and h["captures_waiting"] == 1
