"""Minty's hands on the Second Brain: five tools, the turn hint, and finished missions proposed as knowledge (plugins/builtin/knowledge, app._knowledge_loop)."""
import json
import time

import pytest

from minty_core.app import _knowledge_loop
from minty_core.context import build_context
from minty_core.jobs import Job


@pytest.fixture
def ctx(tmp_path):
    return build_context(tmp_path)


async def call(ctx, name, **args):
    return await ctx.services["call_tool"](name, args)


def j(text):
    return json.loads(text)


async def test_a_brain_dump_through_the_tool_is_filed_split_and_linked_and_says_so(ctx):
    ctx.services["world"].note("organization", "Acme", "size", "50 people", "told", "owner")
    got = j(await call(ctx, "know_capture", text="Idea: sell a box to Acme.\n\nShould we price per kilo?"))
    assert got["status"] == "filed" and [a["kind"] for a in got["pieces"]] == ["idea", "question"] and got["pieces"][0]["about"] == ["Acme"]
    kb = ctx.services["knowledge"]
    assert len(kb.items()) == 2 and kb.sources()[0]["origin"] == "you"


async def test_the_same_tool_called_in_a_turn_that_read_outside_content_only_proposes(ctx):
    ctx.services["turn_read_outside"] = lambda: True
    got = j(await call(ctx, "know_capture", text="Rival charges 12 euros per kilo"))
    assert got["status"] == "review" and "panel" in got["next"].lower() and ctx.services["knowledge"].items() == []


async def test_a_link_is_captured_through_the_safe_page_reader_and_filed_as_outside(ctx):
    class Opened:
        opened, title, text, final_host, note = True, "Margins", "Wholesale margins are lower per bag but volumes are higher.", "example.test", ""

        def summary(self):
            return "ok"

    class Web:
        async def open(self, url, excerpt=3000):
            self.url = url
            return Opened()

    web = ctx.services["web"] = Web()
    got = j(await call(ctx, "know_capture", url="https://example.test/m"))
    assert web.url == "https://example.test/m" and got["status"] == "filed"
    [it] = ctx.services["knowledge"].items()
    assert it["authored"] == "outside" and it["status"] == "unverified"


async def test_something_already_analysed_goes_in_as_a_structured_item_with_its_links_and_its_author(ctx):
    kb = ctx.services["knowledge"]
    src = kb.add_source("text", "interview notes")
    other = kb.add_item("idea", "Monthly box", "x")
    got = j(await call(ctx, "know_capture", kind="insight", title="Cafes reorder monthly", body="Three cafes reorder every month.", tags=["cafes"], sources=[src["id"]],
                       links=[{"rel": "supports", "to": f"item:{other['id']}"}, {"rel": "about", "to": "world:Acme"}]))
    it = kb.item(got["id"])
    assert it["authored"] == "minty" and it["status"] == "unverified" and kb.evidence(it["id"])[0]["source"] == src["id"]
    assert {(l["rel"], l["dst"]) for l in kb.links(f"item:{it['id']}")} == {("supports", f"item:{other['id']}"), ("about", "world:Acme")}
    refused = await call(ctx, "know_capture", kind="person", title="Jane", body="a buyer")
    assert "world" in refused.lower() and len(kb.items()) == 2


async def test_the_same_thing_filed_twice_by_the_structured_path_is_one_item_with_more_evidence(ctx):
    kb = ctx.services["knowledge"]
    s1, s2 = kb.add_source("text", "first telling"), kb.add_source("text", "second telling")
    a = j(await call(ctx, "know_capture", kind="idea", title="Monthly tasting box for small cafes", body="Sell a box.", sources=[s1["id"]]))
    b = j(await call(ctx, "know_capture", kind="idea", title="Monthly tasting box for small cafes", body="Sell a box.", sources=[s2["id"]]))
    assert b["id"] == a["id"] and b["duplicate"] is True and len(kb.items()) == 1 and len(kb.evidence(a["id"])) == 2


async def test_a_structured_item_gets_the_same_name_links_and_proposed_links_as_a_captured_one(ctx):
    ctx.services["world"].note("organization", "Acme", "size", "50 people", "told", "owner")
    kb = ctx.services["knowledge"]
    old = kb.add_item("idea", "Coffee subscription box", "A monthly subscription box of coffee beans for cafes.")
    got = j(await call(ctx, "know_capture", kind="idea", title="Offer Acme a coffee subscription", body="Offer Acme a monthly subscription box of coffee beans for cafes at thirty days."))
    assert got["about"] == ["Acme"] and got["similar"][0]["ref"] == f"item:{old['id']}"
    links = {(l["rel"], l["dst"], l["status"]) for l in kb.links(f"item:{got['id']}", statuses=("confirmed", "suggested"))}
    assert ("about", "world:Acme", "confirmed") in links and ("relates to", f"item:{old['id']}", "suggested") in links


async def test_a_whole_brain_dump_passed_as_one_titled_item_is_still_split_into_its_thoughts(ctx):
    dump = "Idea: sell a monthly tasting box to small cafes.\nLesson learned: always ask the supplier for a volume discount.\nQuestion: Should we invoice at thirty days?"
    got = j(await call(ctx, "know_capture", kind="idea", title="Monthly tasting box for small cafes", body=dump, authored="you"))
    assert got["status"] == "filed" and [a["kind"] for a in got["pieces"]] == ["idea", "lesson", "question"] and len(ctx.services["knowledge"].items()) == 3


async def test_the_older_memory_tools_point_at_the_second_brain_instead_of_saying_there_is_nothing(ctx):
    ctx.services["knowledge"].add_item("lesson", "always ask the supplier for a volume discount before agreeing a price.", "", authored="you")
    for tool in ("memory_audit", "memory_recall"):
        got = await call(ctx, tool, query="prices from suppliers")
        assert "volume discount" in got and "second brain" in got, tool
    ctx.services["knowledge"].add_item("idea", "sell a monthly tasting box to small cafes.", "", authored="you")
    assert "tasting box" in await call(ctx, "memory_recall", query="charging cafes")  # one shared word is enough when the owner asks directly (a turn hint needs two)
    assert await call(ctx, "memory_audit", query="quantum chromodynamics") == "Nothing in memory matches that."  # unchanged when the second brain has nothing either
    ctx.services["is_private"] = lambda: True
    ctx.services["knowledge"].add_item("idea", "Quietly approach a rival about merging", "", private=True)
    assert "merging" in await call(ctx, "memory_audit", query="approach a rival about merging")
    ctx.services["is_private"] = lambda: False
    assert "merging" not in await call(ctx, "memory_audit", query="approach a rival about merging")


async def test_in_a_private_turn_what_is_filed_is_private(ctx):
    ctx.services["is_private"] = lambda: True
    await call(ctx, "know_capture", text="Idea: quietly approach a rival")
    await call(ctx, "know_capture", kind="idea", title="Pitch a merger to the other roaster", body="structured")
    kb = ctx.services["knowledge"]
    assert kb.items() == [] and [i["private"] for i in kb.items(include_private=True)] == [True, True]


async def test_recall_answers_with_who_wrote_it_whether_it_is_checked_and_where_it_came_from(ctx):
    kb = ctx.services["knowledge"]
    src = kb.add_source("link", "spec", uri="https://example.test/spec", origin="outside")
    it = kb.add_item("finding", "Battery lasts twelve hours", "The spec sheet says the battery lasts twelve hours.", sources=[(src["id"], "twelve hours")], outside=True)
    got = j(await call(ctx, "know_recall", query="how long does the battery last"))
    top = got["items"][0]
    assert top["id"] == it["id"] and top["authored"] == "outside" and top["status"] == "unverified" and "not instructions" in got["note"]
    one = j(await call(ctx, "know_recall", ref=f"item:{it['id']}"))
    assert one["evidence"][0]["uri"] == "https://example.test/spec" and one["evidence"][0]["quote"] == "twelve hours" and one["history"][0]["action"] == "added"
    assert j(await call(ctx, "know_recall", query="quantum chromodynamics"))["items"] == []


async def test_links_can_be_made_confirmed_rejected_and_an_outside_turn_can_only_suggest(ctx):
    kb = ctx.services["knowledge"]
    a, b = kb.add_item("idea", "A", ""), kb.add_item("idea", "B", "")
    made = j(await call(ctx, "know_link", src=f"item:{a['id']}", rel="supports", dst=f"item:{b['id']}", why="because"))
    assert made["status"] == "confirmed"
    sug = kb.link(f"item:{b['id']}", "relates to", f"item:{a['id']}", status="suggested", by="auto")
    assert j(await call(ctx, "know_link", id=sug["id"], action="confirm"))["status"] == "confirmed"
    ctx.services["turn_read_outside"] = lambda: True
    again = j(await call(ctx, "know_link", src=f"item:{a['id']}", rel="contradicts", dst=f"item:{b['id']}"))
    assert again["status"] == "suggested"
    assert "outside" in (await call(ctx, "know_link", id=again["id"], action="confirm")).lower()
    ctx.services["turn_read_outside"] = lambda: False
    assert j(await call(ctx, "know_link", id=again["id"], action="reject"))["status"] == "rejected"


async def test_edit_changes_words_status_and_archives_but_nothing_the_model_can_call_deletes(ctx):
    kb = ctx.services["knowledge"]
    it = kb.add_item("finding", "Rent", "Rent is 900.", authored="you", status="verified")
    got = j(await call(ctx, "know_edit", id=it["id"], body="Rent is 950.", tags=["office"]))
    assert got["body"] == "Rent is 950." and got["status"] == "unverified" and got["authored"] == "minty"
    ctx.services["turn_read_outside"] = lambda: True
    assert "outside" in (await call(ctx, "know_edit", id=it["id"], status="verified")).lower()
    ctx.services["turn_read_outside"] = lambda: False
    assert "Nothing to verify" in await call(ctx, "know_edit", id=it["id"], status="verified")
    assert j(await call(ctx, "know_edit", id=it["id"], archive=True))["archived"] is True and kb.items() == []
    assert j(await call(ctx, "know_edit", id=it["id"], archive=False))["archived"] is False
    plugin = next(p for p in ctx.services["list_plugins"]() if p["name"] == "know")
    assert not [n for n in plugin["tools"] if "delete" in n or "remove" in n]


async def test_the_inbox_can_be_reprocessed_and_archived_by_the_tool_but_only_the_owner_can_approve_or_delete(ctx):
    kb = ctx.services["knowledge"]
    c = kb.add_capture("Draft thought about pricing tiers")
    done = j(await call(ctx, "know_edit", id=c["id"], do="reprocess"))
    assert done["status"] == "filed" and len(kb.items()) == 1
    waiting = await ctx.services["knowledge_capture"].capture("From a page", origin="outside")
    assert "owner" in (await call(ctx, "know_edit", id=waiting["id"], do="approve")).lower() and waiting["status"] == "review"
    assert j(await call(ctx, "know_edit", id=waiting["id"], do="archive"))["status"] == "archived"
    assert kb.capture(waiting["id"]) is not None  # archived, not removed


async def test_check_lists_the_gaps_the_conflicts_the_inbox_and_the_health_of_the_brain(ctx):
    kb = ctx.services["knowledge"]
    kb.add_item("question", "Who are the rivals?", "")
    a = kb.add_item("finding", "Rent", "The monthly rent is 900 euros for the office.")
    b = kb.add_item("finding", "Rent now", "The monthly rent is 950 euros for the office.")
    kb.add_capture("waiting")
    gaps = j(await call(ctx, "know_check", what="gaps"))
    assert any(g["kind"] == "open question" for g in gaps["gaps"])
    conflicts = j(await call(ctx, "know_check", what="conflicts"))["conflicts"]
    assert conflicts[0]["verified"] is False and {conflicts[0]["a"], conflicts[0]["b"]} == {a["id"], b["id"]}
    assert j(await call(ctx, "know_check", what="inbox"))["captures"][0]["status"] == "pending"
    assert j(await call(ctx, "know_check"))["health"]["items"] == 3  # the default is the overview
    near = j(await call(ctx, "know_check", what="related", id=a["id"]))["related"]
    assert near and near[0]["id"] == b["id"]


async def test_the_turn_hint_carries_what_the_second_brain_knows_inside_the_one_world_budget(ctx):
    ctx.services["world"].note("organization", "Acme", "size", "50 people", "told", "owner")
    ctx.services["knowledge"].add_item("lesson", "Supplier volume discount", "Always ask the supplier for a volume discount before agreeing a price.", authored="you")
    extras = lambda request: " ".join(h for h in (f(request) for f in ctx.agent.turn_extras) if h)  # noqa: E731
    got = extras("what did I learn about asking Acme's supplier for a discount?")
    assert "Supplier volume discount" in got and "Acme" in got and len(got) <= 760
    assert "Supplier volume discount" not in extras("what is the weather like")
    block = next(h for h in (f("what did I learn about asking Acme's supplier for a discount?") for f in ctx.agent.turn_extras) if "Supplier volume discount" in h)
    assert block.startswith("\n\n")  # a section of its own in the prompt, not run on to the end of the one before it


async def test_a_finished_mission_is_proposed_as_knowledge_once_and_never_filed_by_itself(ctx):
    jobs = ctx.services["jobs"]
    now = time.time()
    plan = json.dumps({"goal": "g", "checkpoints": [{"id": 1, "goal": "collect", "criterion": "20 sources", "passed": True, "verified": True, "evidence": "sheet has 20 rows", "attempts": 1}]})
    jobs.jobs[21] = Job(id=21, kind="mission", goal="Research wholesale cafe margins", status="done", created=now - 60, started=now - 60, result="Margins are 18 percent.", checkpoint={"plan": plan})
    jobs.jobs[22] = Job(id=22, kind="mission", goal="Still going", status="running", created=now - 60, started=now - 60)
    jobs.jobs[23] = Job(id=23, kind="mission", goal="Ancient history", status="done", created=now - 90 * 86400, started=now - 90 * 86400, result="old")

    async def nap(_s):
        return None

    await _knowledge_loop(ctx, first_s=0, every_s=0, rounds=2, sleep=nap)
    kb = ctx.services["knowledge"]
    caps = kb.captures()
    assert len(caps) == 1 and caps[0]["status"] == "review" and caps[0]["origin"] == "minty" and "wholesale cafe margins" in caps[0]["raw"] and "18 percent" in caps[0]["raw"]
    assert kb.items() == []  # waits for the owner
    ctx.services["knowledge_capture"].approve(caps[0]["id"], actor="you")
    [it] = kb.items()
    assert it["authored"] == "minty" and {(l["rel"], l["dst"]) for l in kb.links(f"item:{it['id']}")} == {("derived from", "mission:21")}


def test_there_are_five_tools_and_a_request_about_the_second_brain_offers_them(ctx):
    plugin = next(p for p in ctx.services["list_plugins"]() if p["name"] == "know")
    assert sorted(plugin["tools"]) == ["know_capture", "know_check", "know_edit", "know_link", "know_recall"]
    kits = ctx.agent.toolkits
    assert "know" in kits.select("add this to my second brain") and "know" in kits.select("connect these research notes and find what I know about wholesale")
