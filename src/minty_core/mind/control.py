"""Mission Control: the operational picture, from the real records, and the outcome contract of a mission.

An **outcome contract** is what a mission is for and what would prove it done. A mission already plans as checkpoints, each with a criterion that a verifier checks against
evidence (`agentloop`); this reads that plan as the contract: the objective, the criteria, which are *verified* (evidence showed it), which were only *accepted unchecked*, which
are still to do, what blocks it, and what is next. "Done" is only claimed when every criterion is verified. There is no progress percentage: "3 of 5 criteria verified" is a count,
not an estimate, and a mission that has not planned yet says so.

`overview()` gathers what needs the owner first (questions waiting, blocked missions, stalled work, switched-off workflows, unsettled conflicts), then missions with their contracts,
what else is running, workflows with their last run, recent outcomes and the latest decisions. Nothing is invented: every line comes from a job, a run, the watchdog or a store.
"""
from __future__ import annotations

import json
import time
from typing import Any


def _short(text: str, n: int = 110) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def contract(job) -> dict:
    """The outcome contract of a mission job, read from its saved plan."""
    try:
        plan = json.loads((job.checkpoint or {}).get("plan") or "null")
    except ValueError:
        plan = None
    cps = (plan or {}).get("checkpoints") or [] if isinstance(plan, dict) else []
    criteria, nxt = [], None
    for c in cps:
        state = "verified" if c.get("verified") else "accepted unchecked" if c.get("passed") else "to do"
        criteria.append({"step": _short(c.get("goal"), 90), "criterion": _short(c.get("criterion"), 140), "state": state, "evidence": _short(c.get("evidence"), 140), "attempts": int(c.get("attempts") or 0)})
        if nxt is None and state == "to do":
            nxt = criteria[-1]["step"]
    verified = sum(1 for c in criteria if c["state"] == "verified")
    unchecked = sum(1 for c in criteria if c["state"] == "accepted unchecked")
    done = bool(criteria) and verified == len(criteria)
    if not criteria:
        status = "planning" if job.status == "running" else job.status
    elif done:
        status = "done and verified"
    elif job.status == "done":
        status = "ended, but not every criterion was verified"
    else:
        status = job.status.replace("_", " ")
    return {"job": job.id, "objective": _short(job.goal, 220), "completion": "every criterion below verified by evidence", "status": status, "criteria": criteria,
            "verified": verified, "unchecked": unchecked, "total": len(criteria), "next": nxt, "blocker": _short(job.question, 200) if job.status in ("needs_user", "blocked", "waiting_approval") else "",
            "result": _short(job.result, 240) if job.status in ("done", "failed", "blocked", "timeout", "cancelled") else "", "complete": done}


def overview(services: dict, now: float | None = None) -> dict:
    now = now or time.time()
    jobs = services["jobs"]
    all_jobs = sorted(jobs.jobs.values(), key=lambda j: j.id, reverse=True)
    watchdog, flows, world, approvals = services.get("watchdog"), services.get("flows"), services.get("world"), services.get("approvals")
    missions = [j for j in all_jobs if j.kind == "mission"][:12]
    needs: list[dict[str, Any]] = []
    for ap in (approvals.pending() if approvals is not None else [])[:8]:
        needs.append({"kind": "question", "text": _short(ap.question, 160), "approval": ap.id, "job": ap.job_id})
    for j in missions:
        if j.status in ("needs_user", "blocked"):
            needs.append({"kind": "blocked mission", "text": f"{j.label()}: {_short(j.question or j.result, 140)}", "job": j.id})
    for f in (watchdog.findings() if watchdog is not None else []):
        needs.append({"kind": f["kind"], "text": f["summary"], "verified": f.get("verified", False)})
    flow_rows = []
    if flows is not None:
        for f in flows.store.list():
            runs = flows.store.runs(f.id, 3)
            last = runs[0] if runs else None
            flow_rows.append({"id": f.id, "name": f.name, "enabled": f.enabled, "version": f.active_version, "creator": f.creator, "last": {"run": last["id"], "status": last["status"], "mode": last["mode"], "verified": last["verified"], "error": last["error"][:100]} if last else None})
            if not f.enabled and runs and sum(1 for r in runs if r["mode"] == "real" and r["status"] in ("failed", "blocked")) >= 3:
                needs.append({"kind": "workflow switched off", "text": f"{f.name} failed its last runs and is off", "flow": f.id})
    conflicts = world.conflicts() if world is not None else []
    if conflicts:
        needs.append({"kind": "unsettled conflict", "text": f"{len(conflicts)} fact(s) disagree, e.g. {conflicts[0]['entity']}: {conflicts[0]['attr']}", "verified": True})
    running = [{"id": j.id, "kind": j.kind, "label": j.label(), "goal": _short(j.goal, 80), "status": j.status, "health": j.health, "doing": _short(j.progress[-1] if j.progress else "", 70), "origin": j.origin,
                "seconds": int(now - (j.started or j.created))} for j in all_jobs if j.active and j.kind != "mission"][:10]
    recent = [{"id": j.id, "label": j.label(), "goal": _short(j.goal, 70), "status": j.status, "result": _short(j.result, 120)} for j in all_jobs if j.status in ("done", "failed", "blocked", "timeout", "cancelled", "interrupted")][:8]
    return {"now": now, "needs_you": needs[:14],
            "missions": [{**contract(j), "label": j.label(), "health": j.health, "origin": j.origin, "doing": _short(j.progress[-1] if j.progress else "", 80), "active": j.active} for j in missions],
            "running": running, "flows": flow_rows, "recent": recent,
            "decisions": (world.decisions(limit=5, include_private=False) if world is not None else []),
            "findings": watchdog.findings() if watchdog is not None else []}


def add_routes(api, ctx) -> None:
    from fastapi import HTTPException

    @api.get("/control/overview")
    async def control_overview():
        return overview(ctx.services)

    @api.get("/control/jobs/{job_id}/contract")
    async def control_contract(job_id: int):
        job = ctx.services["jobs"].get(job_id)
        if job is None or job.kind != "mission":
            raise HTTPException(status_code=404, detail="no such mission")
        return contract(job)
