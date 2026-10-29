"""The panel's editor and Minty's tools are two doors into one workflow system (flows/api.py, flows/service.py)."""
import json

import pytest
from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.context import build_context

DOC = {"name": "hand made", "description": "built in the editor", "nodes": [
    {"id": "go", "type": "trigger.manual", "params": {}, "pos": [40, 80]},
    {"id": "a", "type": "data.set", "params": {"values": {"x": "{{input.n}}"}}, "pos": [260, 80]},
    {"id": "r", "type": "out.result", "params": {"value": "{{nodes.a.x}}"}, "pos": [480, 80]}],
    "edges": [{"from": "go", "port": "out", "to": "a"}, {"from": "a", "port": "out", "to": "r"}]}


@pytest.fixture
def ctx(tmp_path):
    return build_context(tmp_path)


@pytest.fixture
def api(ctx):
    with TestClient(create_app(ctx)) as client:  # one event loop for the whole test, like the real server (a run is a job on it)
        client.headers.update({"Authorization": f"Bearer {ctx.token}"})
        yield client


def test_the_editor_needs_the_token_and_sees_the_catalogue(api, ctx):
    assert TestClient(create_app(ctx)).get("/flows").status_code == 401
    cat = api.get("/flows/catalogue").json()
    assert {"trigger.manual", "tool.call", "logic.if", "flow.foreach"} <= {n["type"] for n in cat["nodes"]} and "flows_run" not in cat["tools"]


def test_a_workflow_drawn_in_the_editor_is_saved_checked_listed_and_run(api):
    made = api.post("/flows", json={"doc": DOC}).json()
    assert made["saved"] is True and made["version"] == 1
    listing = api.get("/flows").json()
    assert [f["name"] for f in listing] == ["hand made"] and listing[0]["creator"] == "user"
    got = api.get(f"/flows/{made['id']}").json()
    assert got["doc"]["nodes"][1]["pos"] == [260, 80] and got["version"] == 1  # positions are part of the one definition
    started = api.post(f"/flows/{made['id']}/run", json={"test": True, "input": {"n": 4}}).json()
    assert started["status"] == "running"


async def test_a_run_shows_its_steps_and_an_invalid_draft_says_what_is_wrong(api):
    made = api.post("/flows", json={"doc": DOC}).json()
    run = api.post(f"/flows/{made['id']}/run", json={"input": {"n": 7}}).json()
    import asyncio
    for _ in range(100):
        detail = api.get(f"/flows/runs/{run['run']}").json()
        if detail["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert detail["status"] == "done" and detail["output"] == 7 and [s["status"] for s in detail["steps"]] == ["done", "done", "done"]
    bad = api.post("/flows/check", json={"doc": {**DOC, "edges": DOC["edges"] + [{"from": "r", "port": "out", "to": "a"}]}}).json()
    assert bad["can_run"] is False and any("circle" in i["message"] for i in bad["issues"])
    assert api.put(f"/flows/{made['id']}", json={"doc": {**DOC, "nodes": []}}).json()["saved"] is False
    assert api.get("/flows/9999").status_code == 404


async def test_what_the_agent_changes_the_editor_reads_and_what_the_editor_saves_the_agent_reads(api, ctx):
    made = api.post("/flows", json={"doc": DOC}).json()
    call = ctx.services["call_tool"]
    patched = json.loads(await call("flows_patch", {"flow": made["id"], "note": "added a notification",
                                                    "ops": [{"op": "add_node", "id": "n", "type": "out.notify", "params": {"text": "done"}, "pos": [480, 200]},
                                                            {"op": "connect", "from": "a", "to": "n"}]}))
    assert patched["saved"] and patched["version"] == 2
    seen = api.get(f"/flows/{made['id']}").json()  # the editor reads the agent's edit
    assert [n["id"] for n in seen["doc"]["nodes"]] == ["go", "a", "r", "n"] and seen["versions"][-1]["by"] == "minty"
    seen["doc"]["nodes"][1]["pos"] = [300, 120]  # the user drags a node in the editor and saves
    assert api.put(f"/flows/{made['id']}", json={"doc": seen["doc"], "note": "moved"}).json()["version"] == 3
    got = json.loads(await call("flows_get", {"flow": made["id"]}))
    assert got["doc"]["nodes"][1]["pos"] == [300, 120] and [v["by"] for v in got["versions"]] == ["user", "minty", "user"]


def test_versions_diff_restore_archive_and_enable_are_one_click_each_and_nothing_is_deleted(api):
    made = api.post("/flows", json={"doc": DOC}).json()
    changed = {**DOC, "nodes": DOC["nodes"][:2], "edges": DOC["edges"][:1]}
    api.put(f"/flows/{made['id']}", json={"doc": changed})
    diff = api.get(f"/flows/{made['id']}/versions?a=1&b=2").json()
    assert diff["diff"]["removed_nodes"] == ["r"] and "removed: r" in diff["summary"]
    assert api.post(f"/flows/{made['id']}/restore", json={"version": 1}).json()["version"] == 3
    assert api.post(f"/flows/{made['id']}/enable", json={"enabled": True}).json()["enabled"] is True
    gone = api.post(f"/flows/{made['id']}/archive", json={"archived": True}).json()
    assert gone["archived"] is True and gone["enabled"] is False and api.get("/flows").json() == []
    assert api.get("/flows?archived=true").json()[0]["name"] == "hand made"  # archived is not erased
    assert api.post(f"/flows/{made['id']}/run", json={}).status_code == 400  # and an archived one does not run


def test_the_panel_proxy_lets_the_editor_reach_the_flows_routes():
    from minty_panel.server import ALLOWED

    assert "flows" in ALLOWED  # a page that calls an endpoint the proxy does not list gets "unknown endpoint" and nothing else
