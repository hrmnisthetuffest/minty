"""The panel's HTTP routes for workflows: the editor's whole backend, and nothing but the same `FlowService` the agent's tools use.

Edits made here are saved as the user (`by="user"`), edits the agent makes are saved as `minty`; both are just versions of the one definition, so each side
sees the other's change on its next read. The token and the CSRF header are checked by the router these routes are added to.
"""
from __future__ import annotations

from typing import Any

from fastapi import Body, HTTPException


def add_routes(api, ctx) -> None:
    def svc():
        service = ctx.services.get("flows")
        if service is None:
            raise HTTPException(status_code=503, detail="workflows are not running")
        return service

    def safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'\""))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    def row(f, s):
        runs = s.store.runs(f.id, 1)
        return {"id": f.id, "name": f.name, "creator": f.creator, "enabled": f.enabled, "archived": f.archived, "version": f.active_version, "updated": f.updated,
                "last_run": runs[0] if runs else None}

    @api.get("/flows")
    async def flows_list(archived: bool = False):
        s = svc()
        return [row(f, s) for f in s.store.list(include_archived=archived)]

    @api.get("/flows/catalogue")
    async def flows_catalogue():
        return svc().catalogue()

    @api.get("/flows/runs/{run_id}")
    async def flows_run_detail(run_id: int):
        detail = svc().run_detail(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="no such run")
        return detail

    @api.post("/flows/runs/{run_id}/cancel")
    async def flows_run_cancel(run_id: int):
        run = svc().store.run(run_id)
        if run is None or run["job_id"] is None:
            raise HTTPException(status_code=404, detail="no such run")
        return {"cancelled": bool(await ctx.services["jobs"].cancel(run["job_id"]))}

    @api.post("/flows/runs/{run_id}/resume")
    async def flows_run_resume(run_id: int):
        return safe(svc().resume, run_id)

    @api.post("/flows")
    async def flows_create(body: dict[str, Any] = Body(...)):
        return safe(svc().save, body.get("doc") or {}, by="user", note=str(body.get("note") or ""), creator="user")

    @api.post("/flows/check")
    async def flows_check_draft(body: dict[str, Any] = Body(...)):
        issues = svc().check(body.get("doc") or {})
        return {"can_run": not any(i["level"] == "error" for i in issues), "issues": issues}

    @api.get("/flows/{ref}")
    async def flows_get(ref: str):
        return safe(svc().describe, ref)

    @api.put("/flows/{ref}")
    async def flows_save(ref: str, body: dict[str, Any] = Body(...)):
        return safe(svc().save, body.get("doc") or {}, flow=ref, by="user", note=str(body.get("note") or ""))

    @api.get("/flows/{ref}/runs")
    async def flows_runs(ref: str, limit: int = 20):
        s = svc()
        flow = safe(s._need, ref)
        return {"runs": s.store.runs(flow.id, max(1, min(limit, 100))), "stats": s.store.stats(flow.id)}

    @api.post("/flows/{ref}/run")
    async def flows_run(ref: str, body: dict[str, Any] = Body(default={})):
        return safe(svc().start, ref, mode="test" if body.get("test") else "real", input_=body.get("input"))

    @api.post("/flows/{ref}/enable")
    async def flows_enable(ref: str, body: dict[str, Any] = Body(...)):
        s = svc()
        flow = safe(s._need, ref)
        if body.get("enabled") and any(i["level"] == "error" for i in s.check(s.store.doc(flow.id) or {})):
            raise HTTPException(status_code=400, detail="It has errors, so it cannot be switched on.")
        s.store.set_enabled(flow.id, bool(body.get("enabled")))
        return row(s.store.get(flow.id), s)

    @api.post("/flows/{ref}/archive")
    async def flows_archive(ref: str, body: dict[str, Any] = Body(...)):
        s = svc()
        flow = safe(s._need, ref)
        s.store.set_archived(flow.id, bool(body.get("archived", True)))
        return row(s.store.get(flow.id), s)

    @api.post("/flows/{ref}/restore")
    async def flows_restore(ref: str, body: dict[str, Any] = Body(...)):
        return safe(svc().restore, ref, int(body.get("version") or 0), by="user")

    @api.get("/flows/{ref}/versions")
    async def flows_versions(ref: str, a: int | None = None, b: int | None = None):
        from minty_core.flows import model

        s = svc()
        flow = safe(s._need, ref)
        out: dict[str, Any] = {"versions": s.store.versions(flow.id)}
        if a is not None:
            da, db = s.store.doc(flow.id, a), s.store.doc(flow.id, b if b is not None else flow.active_version)
            if da is None or db is None:
                raise HTTPException(status_code=404, detail="no such version")
            out["diff"] = model.diff(da, db)
            out["summary"] = model.describe_diff(out["diff"])
        return out
