"""Minty knows what it is and what it can do from facts: the identity file, the live capability registry and the map of its own parts (mind/)."""
import json
import re
import tempfile
from types import SimpleNamespace as NS

from minty_core.context import build_context
from minty_core.mind import identity, registry


def host(*plugins):
    return NS(list_plugins=lambda: [{"name": n, "description": d, "enabled": on, "error": err, "tools": tools, "settings_schema": {}} for n, d, on, err, tools in plugins])


def write_trace(tmp_path, *rows):
    folder = tmp_path / "traces"
    folder.mkdir(exist_ok=True)
    (folder / "2026-10-29.jsonl").write_text("\n".join(json.dumps({"kind": "tool_call", **r}) for r in rows) + "\n")


# ---- the capability registry: statuses and evidence, never "has a key = works" --------------------------------------------
def test_each_capability_has_an_honest_status_and_says_how_much_it_has_been_shown_to_work(tmp_path):
    write_trace(tmp_path, *[{"name": "weather_get", "ok": True, "ms": 10}] * 4, *[{"name": "mail_unread", "ok": False, "error": "auth failed", "ms": 5}] * 4)
    h = host(("weather", "Weather and forecasts.", True, None, ["weather_get"]), ("mail", "Read mail.", True, None, ["mail_unread"]),
             ("spotify", "Play music.", False, None, ["spotify_play"]), ("broken", "Never loads.", True, "ImportError: x", []), ("phone", "Message your Telegram.", True, None, ["phone_tell"]))
    snap = registry.snapshot(h, {}, NS(mcp=None), tmp_path, [])
    by = {c["name"]: c for c in snap["capabilities"]}
    assert by["weather"]["status"] == "available" and by["weather"]["evidence"]["calls"] == 4 and by["weather"]["evidence"]["ok"] == 4
    assert by["mail"]["evidence"]["failing"] == ["mail_unread"]  # present and enabled, but it keeps failing
    assert by["spotify"]["status"] == "needs configuration" and "switched off" in by["spotify"]["why"]
    assert by["broken"]["status"] == "unavailable right now" and "failed to load" in by["broken"]["why"]
    assert by["phone"]["status"] == "needs configuration" and "Telegram" in by["phone"]["why"]  # enabled, but nothing to send through
    h2 = host(("weather", "Weather.", True, None, ["weather_get"]))
    assert "not shown to work" in registry.snapshot(h2, {}, NS(mcp=None), tmp_path / "none", [])["capabilities"][0]["evidence"]["note"]  # no traces: said so


def test_a_task_nothing_can_do_does_not_exist_and_one_that_needs_setup_says_what_is_missing(tmp_path):
    h = host(("weather", "Weather and forecasts.", True, None, ["weather_get"]), ("phone", "Message your own Telegram.", True, None, ["phone_tell"]))
    snap = registry.snapshot(h, {}, NS(mcp=None), tmp_path, [])
    nothing = registry.find(snap, "teleport my cat to jupiter")
    assert nothing["status"] == "does not exist" and "pretend" in nothing["note"]
    setup = registry.find(snap, "send me a message on telegram")
    assert setup["status"] == "needs configuration" and setup["matches"][0]["why"].startswith("it needs Telegram")
    assert registry.find(snap, "what is the weather forecast")["status"] == "available"


# ---- the digital twin ------------------------------------------------------------------------------------------------
def test_what_depends_on_a_component_is_found_through_other_components():
    graph = {"edges": [{"from": "plugin:mail", "rel": "needs service", "to": "service:mail_thread", "source": ""},
                       {"from": "plugin:outbox", "rel": "needs service", "to": "service:mail_thread", "source": ""},
                       {"from": "flow:digest", "rel": "runs tool", "to": "tool:mail_unread", "source": ""},
                       {"from": "kit:mail", "rel": "offers", "to": "plugin:mail", "source": ""}]}
    assert registry.impact(graph, "service:mail_thread") == ["kit:mail", "plugin:mail", "plugin:outbox"]
    assert registry.impact(graph, "tool:mail_unread") == ["flow:digest"] and registry.impact(graph, "plugin:unknown") == []


def test_a_missing_service_that_several_parts_cannot_work_without_is_one_verified_finding_and_an_optional_one_is_not(tmp_path):
    nodes = {"service:a": {"running": False}, "service:b": {"running": True}, "service:c": {"running": False}}
    edges = [{"from": "plugin:x", "rel": "needs service", "to": "service:a", "source": ""}, {"from": "plugin:y", "rel": "needs service", "to": "service:a", "source": ""},
             {"from": "plugin:x", "rel": "needs service", "to": "service:b", "source": ""}, {"from": "plugin:z", "rel": "may read service", "to": "service:c", "source": ""}]
    found = registry.diagnose(host(), tmp_path, nodes, edges, [], None)
    assert [(f["about"], f["blocks"], f["verified"]) for f in found] == [("service:a", ["plugin:x", "plugin:y"], True)]


def test_the_real_app_is_mapped_from_its_own_code_and_a_test_file_proxy_is_marked_a_hypothesis(tmp_path):
    ctx = build_context(tmp_path)
    s = ctx.services
    graph = registry.twin(NS(list_plugins=s["list_plugins"]), s, s["config"](), tmp_path, s["flows"], s["router_status"](), None)
    kinds = {c["kind"] for c in graph["components"]}
    assert {"plugin", "background loop", "service", "tool group", "model provider"} <= kinds and len(graph["edges"]) > 50
    assert any(e["rel"] == "needs service" and e["source"] == "the plugin's source" for e in graph["edges"])  # derived from code, not written by hand
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_weather.py").write_text("")
    guesses = registry.diagnose(NS(list_plugins=s["list_plugins"]), tmp_path, {}, [], [], tests)
    flagged = [g for g in guesses if g["kind"] == "no test file mentions it"]
    assert flagged and all(g["verified"] is False for g in flagged)


# ---- the identity file -------------------------------------------------------------------------------------------------
def test_the_identity_files_generated_facts_are_current_and_say_what_the_code_has():
    text = identity.load()
    assert identity.is_current(text), "run scripts/gen_identity.py"
    facts = identity.static_facts()
    assert "workflow node types" in facts and "`self`" in facts and "`flows`" in facts and "tool.call" in facts


def test_the_identity_file_holds_no_personal_data_or_secrets_and_has_every_section():
    text = identity.load()
    assert not re.search(r"@[a-z0-9-]+\.[a-z]{2,}|sk-[A-Za-z0-9]{10,}|ck_[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_-]{20,}|xox[bp]-|ghp_[A-Za-z0-9]{20,}|hrmn|archlinux", text), "no personal data or keys"
    assert sorted(identity.sections(text)) == list("ABCDEFGH")
    assert "personality" in identity.sections(text)["C"].lower()  # tone is the personality store's, not overridden here


def test_the_introductions_come_from_one_source_and_differ_by_who_asks():
    live = {"tools": 300, "capabilities": [{"name": "weather", "status": "available", "tools": ["weather_get"], "evidence": {"calls": 3}},
                                           {"name": "phone", "status": "needs configuration", "tools": ["phone_tell"], "evidence": {"calls": 0}}],
            "counts": {"available": 1, "needs configuration": 1}}
    short, detailed, technical, user, agent = (identity.introduce(level, live) for level in identity.LEVELS)
    assert len(short) < 700 and "personal operating agent" in short and len(detailed) > len(short) * 2
    assert "Version" in technical and "background loops" in technical and "Right now on this machine" in detailed
    assert "Limits" in user
    data = json.loads(agent)  # for another AI: structured, and only what is available now is offered as a capability
    assert data["name"] == "Minty" and [c["capability"] for c in data["capabilities_now"]] == ["weather"] and data["not_available_now"] == ["phone"]
    assert identity.introduce("nonsense") == identity.introduce("short")


# ---- the tools --------------------------------------------------------------------------------------------------------------
async def test_minty_introduces_itself_and_reports_what_it_can_really_do_through_its_own_tools(tmp_path):
    ctx = build_context(tmp_path)
    call = ctx.services["call_tool"]
    assert "personal operating agent" in await call("self_about", {"level": "short"})
    assert "Right now on this machine" in await call("self_about", {"level": "detailed"})
    over = json.loads(await call("self_can", {}))
    assert over["tools"] > 250 and "needs_attention" in over
    missing = json.loads(await call("self_can", {"task": "send me a message on telegram"}))
    assert missing["status"] == "needs configuration" and missing["matches"][0]["capability"] == "phone"  # Telegram is not running here: it does not claim it works
    assert json.loads(await call("self_can", {"task": "teleport my cat to jupiter"}))["status"] == "does not exist"
    diag = json.loads(await call("self_diagnose", {}))
    assert diag["components"] > 100 and diag["findings"] and "verified" in diag
    effect = json.loads(await call("self_diagnose", {"component": "service:flows"}))
    assert "plugin:flows" in effect["depends_on_it"]


def test_nothing_the_model_can_call_edits_the_identity_file_so_a_page_cannot_redefine_minty():
    ctx = build_context(tempfile.mkdtemp())
    names = [t for p in ctx.services["list_plugins"]() for t in p["tools"]]
    assert sorted(t for t in names if t.startswith("self_")) == ["self_about", "self_can", "self_diagnose"]  # read-only: no write path to who Minty is
