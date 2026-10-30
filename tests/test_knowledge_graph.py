"""The knowledge universe and the routes the panel reads and writes it through: every node is a real record, nothing private leaks, nothing outside confirms itself (knowledge/graph.py, knowledge/api.py)."""
import base64
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.context import build_context
from minty_core.jobs import Job
from minty_core.knowledge import graph


class FakeVault:
    """Just enough of the notes folder for the graph: two notes and a link between them."""

    def active(self):
        return True

    def kit(self):
        rows = [{"id": "n1", "title": "Supplier call", "tags": ["suppliers"], "project": "Coffee", "confidence": "verified", "updated": "2026-10-20T10:00:00", "path": "notes/a.md", "archived": False},
                {"id": "n2", "title": "Price list", "tags": [], "project": "Coffee", "confidence": "untrusted", "updated": "2026-10-21T10:00:00", "path": "sources/b.md", "archived": False}]
        return NS(vault=NS(index=NS(rows=lambda folder=None, include_archived=False, limit=200: rows, link_pairs=lambda: [("n1", "n2")])))


@pytest.fixture
def ctx(tmp_path):
    return build_context(tmp_path)


def ref(it):
    return f"item:{it['id']}"


def seeded(ctx):
    s = ctx.services
    kb, world = s["knowledge"], s["world"]
    world.note("organization", "Acme", "size", "50 people", "told", "owner")
    world.note("person", "Jane", "role", "buyer at Acme", "told", "owner")
    world.relate("person", "Jane", "works_at", "organization", "Acme", "told", "owner")
    world.decide("Focus on wholesale first", "decided", rationale="margins", source="owner", entities=["Acme"])
    src = kb.add_source("link", "Wholesale margins are lower per bag.", title="Margins article", uri="https://example.test/m", origin="outside")
    a = kb.add_item("finding", "Wholesale margins are lower", "Lower per bag, higher volume.", project="Coffee", sources=[src["id"]], outside=True)
    b = kb.add_item("idea", "Monthly box for cafes", "A subscription.", project="Coffee")
    c = kb.add_item("question", "Who are the rivals?", "", project="Coffee")
    d = kb.add_item("note", "Lonely thought", "x")
    kb.link(ref(b), "supports", ref(a))
    kb.link(ref(b), "about", "world:Acme")
    kb.link(ref(c), "relates to", ref(a), status="suggested", by="auto", score=0.4)
    kb.link(ref(d), "relates to", ref(a), status="suggested", by="auto")
    kb.set_link(kb.links(statuses=("suggested",))[1]["id"], "rejected")
    return {"src": src, "a": a, "b": b, "c": c, "d": d}


def by_id(g):
    return {n["id"]: n for n in g["nodes"]}


def test_an_empty_brain_is_an_empty_graph_that_says_so(ctx):
    g = graph.build(ctx.services)
    assert g["nodes"] == [] and g["edges"] == [] and g["empty"] is True and g["clusters"] == []


def test_every_node_is_a_real_record_from_the_store_it_lives_in_and_keeps_its_provenance(ctx):
    k = seeded(ctx)
    nodes = by_id(graph.build(ctx.services))
    assert nodes[ref(k["a"])]["system"] == "knowledge" and nodes[ref(k["a"])]["authored"] == "outside" and nodes[ref(k["a"])]["status"] == "unverified"
    assert nodes[f"source:{k['src']['id']}"]["kind"] == "source" and nodes[f"source:{k['src']['id']}"]["label"] == "Margins article"
    assert nodes["world:Acme"]["system"] == "world" and nodes["world:Acme"]["kind"] == "organization" and nodes["world:Jane"]["kind"] == "person"
    decision = next(n for n in nodes.values() if n["kind"] == "decision")
    assert decision["label"] == "Focus on wholesale first" and decision["system"] == "world"
    assert not [n for n in nodes.values() if n["kind"] == "mission"]  # work appears only when something links to it


def test_edges_are_confirmed_or_suggested_never_rejected_and_connect_across_stores(ctx):
    k = seeded(ctx)
    g = graph.build(ctx.services)
    edges = {(e["a"], e["rel"], e["b"]): e for e in g["edges"]}
    assert edges[(ref(k["b"]), "supports", ref(k["a"]))]["status"] == "confirmed"
    assert edges[(ref(k["c"]), "relates to", ref(k["a"]))]["status"] == "suggested" and edges[(ref(k["c"]), "relates to", ref(k["a"]))]["score"] == 0.4
    assert (ref(k["d"]), "relates to", ref(k["a"])) not in edges  # the owner said no
    assert edges[(ref(k["b"]), "about", "world:Acme")]["status"] == "confirmed" and edges[("world:Jane", "works_at", "world:Acme")]["status"] == "confirmed"
    assert edges[(ref(k["a"]), "derived from", f"source:{k['src']['id']}")]["status"] == "confirmed"
    dec = next(n["id"] for n in g["nodes"] if n["kind"] == "decision")
    assert (dec, "about", "world:Acme") in edges


def test_the_notes_folder_joins_the_universe_when_it_is_on(ctx):
    seeded(ctx)
    ctx.services["vault"] = FakeVault()
    g = graph.build(ctx.services)
    nodes = by_id(g)
    assert nodes["vault:n1"]["system"] == "vault" and nodes["vault:n1"]["kind"] == "note" and nodes["vault:n2"]["authored"] == "outside"
    assert any(e["a"] == "vault:n1" and e["b"] == "vault:n2" and e["status"] == "confirmed" for e in g["edges"])


def test_missions_and_workflows_appear_when_a_link_points_at_them(ctx):
    k = seeded(ctx)
    ctx.services["jobs"].jobs[7] = Job(id=7, kind="mission", goal="Research wholesale cafe margins", status="done", created=time.time(), started=time.time())
    ctx.services["knowledge"].link(ref(k["a"]), "derived from", "mission:7")
    nodes = by_id(graph.build(ctx.services))
    assert nodes["mission:7"]["label"].startswith("Research wholesale") and nodes["mission:7"]["system"] == "work" and nodes["mission:7"]["kind"] == "mission"


def test_nothing_private_is_in_the_default_view_and_it_is_there_when_asked(ctx):
    k = seeded(ctx)
    kb, world = ctx.services["knowledge"], ctx.services["world"]
    hidden = kb.add_item("idea", "Quietly approach a rival", "secret", private=True)
    kb.link(ref(hidden), "relates to", ref(k["b"]))
    world.note("person", "Mole", "role", "informant", "told", "owner", private=True)
    g = graph.build(ctx.services)
    assert ref(hidden) not in by_id(g) and "world:Mole" not in by_id(g) and not [e for e in g["edges"] if ref(hidden) in (e["a"], e["b"])]
    assert "secret" not in str(g) and "Quietly" not in str(g)
    both = graph.build(ctx.services, include_private=True)
    assert ref(hidden) in by_id(both) and "world:Mole" in by_id(both)
    assert not graph.find(ctx.services, "quietly approach") and graph.find(ctx.services, "quietly approach", include_private=True)


def test_a_private_original_and_a_private_item_cannot_be_opened_or_reached_from_a_public_one(ctx):
    kb = ctx.services["knowledge"]
    secret = kb.add_source("text", "the private minutes", title="Private minutes", private=True)
    open_item = kb.add_item("note", "Public thought", "x", sources=[secret["id"]])
    hidden = kb.add_item("idea", "Hidden idea", "y", private=True)
    g = graph.build(ctx.services)
    assert f"source:{secret['id']}" not in by_id(g) and ref(open_item) in by_id(g)
    assert graph.node(ctx.services, ref(hidden)) is None and graph.node(ctx.services, f"source:{secret['id']}") is None
    assert graph.node(ctx.services, ref(hidden), include_private=True)["title"] == "Hidden idea"
    assert f"source:{secret['id']}" in by_id(graph.build(ctx.services, include_private=True))


def test_a_mission_only_an_unseen_item_pointed_at_is_not_drawn_alone(ctx):
    kb = ctx.services["knowledge"]
    ctx.services["jobs"].jobs[9] = Job(id=9, kind="mission", goal="Some mission", status="done", created=time.time(), started=time.time())
    gone = kb.add_item("note", "Archived", "x")
    kb.link(ref(gone), "derived from", "mission:9")
    kb.archive(gone["id"])
    assert "mission:9" not in by_id(graph.build(ctx.services))


def test_a_connection_to_a_mission_a_decision_or_a_note_is_named_not_shown_as_a_reference(ctx):
    k = seeded(ctx)
    ctx.services["vault"] = FakeVault()
    ctx.services["jobs"].jobs[7] = Job(id=7, kind="mission", goal="Research wholesale cafe margins", status="done", created=time.time(), started=time.time())
    dec = next(n["id"] for n in graph.build(ctx.services)["nodes"] if n["kind"] == "decision")
    kb = ctx.services["knowledge"]
    kb.link(ref(k["a"]), "derived from", "mission:7")
    kb.link(ref(k["a"]), "relates to", dec)
    kb.link(ref(k["a"]), "relates to", "vault:n1")
    labels = {l["ref"]: l["label"] for l in graph.node(ctx.services, ref(k["a"]))["links"]}
    assert labels["mission:7"] == "Research wholesale cafe margins" and labels[dec] == "Focus on wholesale first" and labels["vault:n1"] == "Supplier call"


def test_archived_items_leave_the_universe(ctx):
    k = seeded(ctx)
    ctx.services["knowledge"].archive(k["b"]["id"])
    assert ref(k["b"]) not in by_id(graph.build(ctx.services))


def test_clusters_gather_what_belongs_together_and_name_themselves(ctx):
    k = seeded(ctx)
    g = graph.build(ctx.services)
    nodes = by_id(g)
    coffee = {nodes[ref(k[x])]["cluster"] for x in "abc"}
    assert len(coffee) == 1 and next(c for c in g["clusters"] if c["id"] in coffee)["label"] == "Coffee"
    assert nodes[ref(k["d"])]["cluster"] == "_loose" and sum(c["size"] for c in g["clusters"]) == len(g["nodes"])


def test_size_follows_confirmed_connections_and_glow_follows_recency(ctx):
    k = seeded(ctx)
    nodes = by_id(graph.build(ctx.services))
    assert nodes[ref(k["a"])]["degree"] >= 2 and nodes[ref(k["c"])]["degree"] == 0  # a suggestion is not a connection
    assert nodes[ref(k["a"])]["size"] > nodes[ref(k["c"])]["size"] and 0.9 < nodes[ref(k["a"])]["glow"] <= 1.0
    old = graph.build(ctx.services, now=time.time() + 30 * 86400)
    assert by_id(old)[ref(k["a"])]["glow"] == 0


def test_a_large_universe_keeps_the_most_connected_and_the_one_in_focus_and_says_what_it_left_out(ctx):
    kb = ctx.services["knowledge"]
    hub = kb.add_item("concept", "Hub", "x")
    items = [kb.add_item("note", f"Thought {i}", f"thought number {i}") for i in range(30)]
    for it in items[:8]:
        kb.link(ref(it), "part of", ref(hub))
    far = items[-1]
    g = graph.build(ctx.services, max_nodes=10, focus=ref(far))
    nodes = by_id(g)
    assert len(nodes) == 10 and ref(hub) in nodes and ref(far) in nodes and g["truncated"] == 21
    assert all(e["a"] in nodes and e["b"] in nodes for e in g["edges"])


def test_search_finds_items_people_sources_and_notes_for_highlighting(ctx):
    k = seeded(ctx)
    ctx.services["vault"] = FakeVault()
    found = {h["ref"]: h for h in graph.find(ctx.services, "wholesale margins")}
    assert ref(k["a"]) in found and f"source:{k['src']['id']}" in found
    assert "world:Acme" in {h["ref"] for h in graph.find(ctx.services, "acme")}
    assert "vault:n1" in {h["ref"] for h in graph.find(ctx.services, "supplier call")}
    assert graph.find(ctx.services, "quantum chromodynamics") == []


def test_opening_a_node_shows_the_original_the_evidence_the_links_and_the_history(ctx):
    k = seeded(ctx)
    got = graph.node(ctx.services, ref(k["a"]))
    assert got["kind"] == "finding" and got["authored"] == "outside" and got["note"] and got["evidence"][0]["uri"] == "https://example.test/m"
    assert {(l["rel"], l["label"]) for l in got["links"]} >= {("is supports of", "Monthly box for cafes")} or any(l["label"] == "Monthly box for cafes" for l in got["links"])
    assert got["history"][0]["action"] == "added" and got["original"]["text"].startswith("Wholesale margins")
    acme = graph.node(ctx.services, "world:Acme")
    assert acme["system"] == "world" and acme["facts"][0]["value"] == "50 people" and any(l["label"] == "Monthly box for cafes" for l in acme["links"])
    assert graph.node(ctx.services, "item:nope") is None and graph.node(ctx.services, "bogus") is None


# ---- the routes -------------------------------------------------------------------------------------------------------------
@pytest.fixture
def client(ctx):
    with TestClient(create_app(ctx)) as c:
        c.headers["Authorization"] = f"Bearer {ctx.token}"
        yield c


def test_the_routes_need_the_token_and_the_panel_proxy_lets_them_through(ctx):
    assert TestClient(create_app(ctx)).get("/knowledge/graph").status_code == 401
    from minty_panel.server import ALLOWED

    assert "knowledge" in ALLOWED


def test_the_panel_reads_the_universe_a_node_a_search_and_what_needs_attention(client, ctx):
    k = seeded(ctx)
    g = client.get("/knowledge/graph").json()
    assert {n["id"] for n in g["nodes"]} >= {ref(k["a"]), "world:Acme"} and g["clusters"]
    assert client.get("/knowledge/node", params={"ref": ref(k["a"])}).json()["title"] == "Wholesale margins are lower"
    assert client.get("/knowledge/node", params={"ref": "item:nope"}).status_code == 404
    assert client.get("/knowledge/search", params={"q": "wholesale"}).json()["hits"][0]["ref"] in {ref(k["a"]), f"source:{k['src']['id']}"}
    att = client.get("/knowledge/attention").json()
    assert {"health", "gaps", "conflicts", "captures"} <= set(att) and any(g["kind"] == "open question" for g in att["gaps"])
    assert client.get("/knowledge/graph", params={"private": "1"}).status_code == 200
    ctx.services["knowledge"].add_capture("a private thought waiting", private=True)
    assert "private thought" not in str(client.get("/knowledge/attention").json()) and "private thought" in str(client.get("/knowledge/attention", params={"private": "1"}).json())


def test_the_owner_can_drop_text_a_link_or_a_file_into_the_inbox_from_the_panel(client, ctx):
    got = client.post("/knowledge/captures", json={"text": "Idea: sell tasting boxes.\n\nShould we price per kilo?"}).json()
    assert got["status"] == "filed" and len(got["result"]["atoms"]) == 2 and ctx.services["knowledge"].items()[0]["authored"] == "you"
    up = client.post("/knowledge/captures/upload", json={"name": "notes.txt", "data": base64.b64encode(b"Meeting notes: Acme wants weekly deliveries and invoices at thirty days.").decode()}).json()
    assert up["status"] == "filed" and any(s["kind"] == "file" for s in ctx.services["knowledge"].sources())
    assert client.post("/knowledge/captures/upload", json={"name": "big.bin", "data": base64.b64encode(b"x" * (9 * 1024 * 1024)).decode()}).status_code == 413
    assert client.post("/knowledge/captures", json={"text": "   "}).status_code == 400


def test_the_owner_approves_edits_reprocesses_archives_and_deletes_inbox_entries(client, ctx):
    cap = ctx.services["knowledge_capture"]
    import asyncio

    waiting = asyncio.run(cap.capture("Competitors charge 12 euros", origin="outside", process=True))
    assert waiting["status"] == "review"
    ok = client.post(f"/knowledge/captures/{waiting['id']}/approve").json()
    assert ok["status"] == "filed" and ctx.services["knowledge"].items()[0]["authored"] == "outside"
    other = ctx.services["knowledge"].add_capture("Draft about pricing")
    assert client.patch(f"/knowledge/captures/{other['id']}", json={"text": "Draft about pricing tiers"}).json()["raw"] == "Draft about pricing tiers"
    assert client.post(f"/knowledge/captures/{other['id']}/process").json()["status"] == "filed"
    assert client.post(f"/knowledge/captures/{other['id']}/archive").json()["status"] == "archived"
    assert client.delete(f"/knowledge/captures/{other['id']}").status_code == 200 and ctx.services["knowledge"].capture(other["id"]) is None
    assert client.delete("/knowledge/captures/c-missing").status_code == 404


def test_the_owner_edits_verifies_and_archives_items_and_makes_and_judges_links(client, ctx):
    k = seeded(ctx)
    kb = ctx.services["knowledge"]
    assert client.patch(f"/knowledge/items/{k['b']['id']}", json={"status": "verified"}).json()["status"] == "verified"  # the owner can say it is checked
    assert client.patch(f"/knowledge/items/{k['b']['id']}", json={"title": "Monthly tasting box"}).json()["title"] == "Monthly tasting box"
    assert client.patch(f"/knowledge/items/{k['b']['id']}", json={"archive": True}).json()["archived"] is True
    made = client.post("/knowledge/links", json={"src": ref(k["c"]), "rel": "answers", "dst": ref(k["a"])}).json()
    assert made["status"] == "confirmed" and made["by"] == "you" if "by" in made else made["status"] == "confirmed"
    sug = kb.links(statuses=("suggested",))[0]
    assert client.post(f"/knowledge/links/{sug['id']}/confirm").json()["status"] == "confirmed"
    assert client.post(f"/knowledge/links/{sug['id']}/reject").json()["status"] == "rejected"
    new = client.post("/knowledge/items", json={"kind": "idea", "title": "Try a pop-up", "body": "x"}).json()
    assert new["authored"] == "you" and client.post("/knowledge/items", json={"kind": "idea", "title": "Try a pop-up", "body": "x"}).json()["id"] == new["id"]  # the same thing again is not a second copy and client.post("/knowledge/items", json={"kind": "person", "title": "Jane"}).status_code == 400
    assert client.delete(f"/knowledge/items/{k['a']['id']}").status_code in (404, 405)  # there is no way to delete knowledge, only to archive it
