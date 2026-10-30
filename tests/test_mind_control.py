"""Outcome contracts and Mission Control: what would prove a mission done, and an overview built only from real records (mind/control.py)."""
import json

import pytest
from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.context import build_context
from minty_core.jobs import Job
from minty_core.mind.control import contract, overview


def plan(*cps):
    return json.dumps({"goal": "g", "checkpoints": [{"id": i + 1, "goal": g, "criterion": c, "passed": p, "verified": v, "evidence": e, "attempts": a} for i, (g, c, p, v, e, a) in enumerate(cps)]})


def mission(id_, status="running", cps=(), **kw):
    return Job(id=id_, kind="mission", goal="Research the market and rank the best fits", status=status, created=1000.0, started=1000.0, checkpoint={"plan": plan(*cps)} if cps else {}, **kw)


def test_a_mission_is_done_only_when_every_criterion_is_verified_by_evidence():
    j = mission(1, "done", [("collect", "20 sources", True, True, "sheet has 20 rows", 1), ("rank", "ranked table", True, False, "", 1), ("write", "one page", True, True, "doc exists", 1)])
    c = contract(j)
    assert c["complete"] is False and c["verified"] == 2 and c["unchecked"] == 1 and c["status"] == "ended, but not every criterion was verified"
    assert [x["state"] for x in c["criteria"]] == ["verified", "accepted unchecked", "verified"]
    full = contract(mission(2, "done", [("collect", "20 sources", True, True, "ok", 1), ("write", "one page", True, True, "ok", 1)]))
    assert full["complete"] is True and full["status"] == "done and verified" and full["completion"].startswith("every criterion")


def test_a_running_mission_shows_what_is_done_what_is_next_and_what_blocks_it_without_a_made_up_percentage():
    c = contract(mission(3, "running", [("collect", "20 sources", True, True, "ok", 1), ("rank", "ranked table", False, False, "", 2)]))
    assert c["status"] == "running" and c["next"] == "rank" and c["criteria"][1]["attempts"] == 2 and "percent" not in json.dumps(c).lower() and c["total"] == 2
    assert contract(mission(4, "running"))["status"] == "planning"  # no plan yet: it says so instead of inventing steps
    blocked = contract(mission(5, "blocked", question="Which account first?"))
    assert blocked["blocker"] == "Which account first?" and blocked["complete"] is False


@pytest.fixture
def ctx(tmp_path):
    return build_context(tmp_path)


def test_the_overview_puts_what_needs_the_owner_first_and_every_line_comes_from_a_record(ctx):
    s = ctx.services
    s["jobs"].jobs[7] = mission(7, "blocked", question="Which account first?", result="Which account first?")
    s["jobs"].jobs[8] = mission(8, "running", [("collect", "20 sources", True, True, "ok", 1)], progress=["step 1", "using mcp call"])
    s["jobs"].jobs[9] = Job(id=9, kind="flow", goal="records check", status="running", created=1000.0, started=1000.0, touched=1000.0, health="stalled", progress=["get running"])
    s["approvals"].request("mail_send", {"n": 1}, "Send the report to the team?", 3)
    s["world"].note("organization", "Acme", "size", "50 people", "discovered", "a site")
    s["world"].note("organization", "Acme", "size", "200 people", "discovered", "an article")
    s["world"].decide("Focus on wholesale first", "decided", rationale="margins", source="the owner")
    o = overview(s, now=1500.0)
    kinds = [n["kind"] for n in o["needs_you"]]
    assert kinds[0] == "question" and "blocked mission" in kinds and "unsettled conflict" in kinds
    assert [m["job"] for m in o["missions"]] == [8, 7] and o["missions"][0]["active"] is True and o["missions"][0]["doing"] == "using mcp call"
    assert [(r["id"], r["health"], r["seconds"]) for r in o["running"]] == [(9, "stalled", 500)]
    assert o["decisions"][0]["title"] == "Focus on wholesale first" and o["recent"][0]["status"] == "blocked"


def test_a_workflow_the_watchdog_switched_off_is_named_as_needing_the_owner(ctx):
    s = ctx.services
    doc = {"name": "digest", "nodes": [{"id": "go", "type": "trigger.manual", "params": {}, "pos": [0, 0]}], "edges": []}
    f = s["flows"].store.create(doc)
    for _ in range(3):
        rid = s["flows"].store.start_run(f.id, 1, "real", "schedule", {})
        s["flows"].store.finish_run(rid, "failed", error="smtp refused")
    o = overview(s)
    assert any(n["kind"] == "workflow switched off" and n["flow"] == f.id for n in o["needs_you"]) and o["flows"][0]["last"]["status"] == "failed"


def test_the_panel_reads_it_over_the_api_and_one_mission_can_be_asked_about_by_id(ctx):
    ctx.services["jobs"].jobs[11] = mission(11, "running", [("collect", "20 sources", True, True, "ok", 1)])
    with TestClient(create_app(ctx)) as client:
        client.headers["Authorization"] = f"Bearer {ctx.token}"
        assert TestClient(create_app(ctx)).get("/control/overview").status_code == 401
        assert client.get("/control/overview").json()["missions"][0]["objective"].startswith("Research the market")
        assert client.get("/control/jobs/11/contract").json()["verified"] == 1 and client.get("/control/jobs/999/contract").status_code == 404
    from minty_panel.server import ALLOWED

    assert "control" in ALLOWED  # the proxy would otherwise answer "unknown endpoint"


async def test_minty_can_read_a_missions_contract_through_its_own_tool(ctx):
    ctx.services["jobs"].jobs[12] = mission(12, "running", [("collect", "20 sources", True, True, "ok", 1), ("rank", "ranked table", False, False, "", 1)])
    got = json.loads(await ctx.services["call_tool"]("missions_contract", {"id": 12}))
    assert got["next"] == "rank" and got["complete"] is False
    assert await ctx.services["call_tool"]("missions_contract", {"id": 999}) == "No such mission."
