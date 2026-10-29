"""The workflow system as the rest of Minty uses it: one object that the agent's tools, the panel's editor, the scheduler and the job events all go through.

It owns the rules that must be the same everywhere: a workflow is validated the same way on save and on run; a run is a background job (so it has a time
budget that leaves out waiting for you, can be cancelled, resumes after a restart and asks its questions where the request was made); every tool step goes
through the mission worker, i.e. the same safety gate, receipts, secret checks and provenance as everything else, with its own confirm that parks the job for
your answer; and a run is pinned to the version it started on, so editing a workflow never changes a run that is going.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Awaitable, Callable

from minty_core.flows import model
from minty_core.flows.engine import MAX_CHILD_DEPTH, Env, RunResult, diagnose, execute
from minty_core.flows.store import FlowStore
from minty_core.jobs import JobOutcome
from minty_core.origin import current_origin
from minty_core.policy import classify

RUN_BUDGET_S = 3600.0  # seconds of work, not counting the time spent waiting for your answers
APPROVAL_TTL_S = 900.0
APPROVAL_WAIT_S = 6 * 3600.0
BAD = ("failed", "blocked", "timeout")
EXAMPLE = {"name": "size check", "nodes": [
    {"id": "start", "type": "trigger.manual", "params": {}, "pos": [40, 40]},
    {"id": "big", "type": "logic.if", "params": {"where": {"path": "input.n", "op": "gt", "value": 10}}, "pos": [260, 40]},
    {"id": "yes", "type": "out.result", "params": {"value": "n is above 10"}, "pos": [480, 0]},
    {"id": "no", "type": "out.result", "params": {"value": "n is 10 or less"}, "pos": [480, 90]}],
    "edges": [{"from": "start", "port": "out", "to": "big"}, {"from": "big", "port": "true", "to": "yes"}, {"from": "big", "port": "false", "to": "no"}]}


class FlowService:
    def __init__(self, store: FlowStore, *, jobs, worker_factory: Callable, get_tool: Callable, tool_names: Callable[[], list[str]],
                 ask: Callable[[str, str], Awaitable[str]], notify: Callable[[str], None], emit: Callable[[dict], None] = lambda e: None,
                 clock: Callable[[], float] = time.time):
        self.store, self._jobs, self._worker, self._get_tool, self._tool_names = store, jobs, worker_factory, get_tool, tool_names
        self._ask, self._notify, self._emit, self._clock = ask, notify, emit, clock
        for run in store.interrupted():  # the app stopped while these were going: they can be resumed, and say so
            store.finish_run(run["id"], "interrupted", error="The app stopped while it ran. Resume it to carry on from the last finished step.")
        jobs.register_resumer("flow", self._resumer)

    # ---- what exists -------------------------------------------------------------------------------------------------
    def tool_exists(self, name: str) -> bool:
        return not str(name).startswith("flows_") and self._get_tool(name) is not None

    def flow_exists(self, ref: str) -> bool:
        f = self.store.get(ref)
        return f is not None and not f.archived

    def catalogue(self, compact: bool = False) -> dict:
        """What can be built. The editor gets every setting of every node; the agent (compact) gets the settings that are special to a node, the ones every
        node has once, and a small complete example, so it does not spend its turns guessing the shape."""
        common = set(model.COMMON)
        return {"nodes": [{"type": s.type, "title": s.title, "group": s.group, "doc": s.doc, "ports": list(s.ports) + (["error"] if not s.trigger else []),
                           "effect": s.effect, "trigger": s.trigger,
                           "params": {k: ({"kind": v[0], "required": v[1]} if not compact else f"{v[0]}{'*' if v[1] else ''}") for k, v in s.params.items() if not (compact and k in common)}}
                          for s in model.CATALOGUE.values()],
                "tools": sorted(n for n in self._tool_names() if self.tool_exists(n)),
                "every_node": "retry {max 0-3, delay_s} (transient failures of steps that change nothing only), timeout_s 1-300, label, note; connect an `error` port to handle a failure",
                "expressions": "{{input.x}} {{nodes.<id>.field}} {{prev.field}} {{item.field}} {{index}}; a condition is {\"path\": \"input.n\", \"op\": \"gt\", \"value\": 10} "
                               "or {all|any: [..]} or {not: ..}; op is one of " + ", ".join(model.OPS) + " (words, not symbols)",
                "example": EXAMPLE}

    def check(self, doc: dict) -> list[dict]:
        issues = model.validate(doc, tool_exists=self.tool_exists, flow_exists=self.flow_exists)
        return issues + (model.lint(doc) if not any(i["level"] == "error" for i in issues) else [])

    # ---- writing definitions (the agent and the editor use these two) -----------------------------------------------------
    def save(self, doc: dict, *, flow: str | int | None = None, by: str = "user", note: str = "", creator: str | None = None) -> dict:
        """Create a workflow, or add a new version to one. It is saved only if it has no errors; warnings and hints come back with it."""
        doc = model.normalize(doc)
        existing = self.store.get(flow) if flow is not None else None
        if flow is not None and existing is None:
            raise LookupError(f"There is no workflow {flow}.")
        issues = self.check(doc)
        errors = [i for i in issues if i["level"] == "error"]
        if errors:
            return {"saved": False, "issues": issues}
        if existing is None:
            f = self.store.create(doc, creator=creator or by, by=by, note=note)
            return {"saved": True, "id": f.id, "name": f.name, "version": f.active_version, "issues": issues}
        number = self.store.add_version(existing.id, doc, by=by, note=note or "edited")
        return {"saved": True, "id": existing.id, "name": doc["name"], "version": number, "issues": issues,
                "changes": model.describe_diff(model.diff(self.store.doc(existing.id, existing.active_version) or {}, doc))}

    def patch(self, ref, ops: list, *, by: str = "user", note: str = "") -> dict:
        flow = self._need(ref)
        before = self.store.doc(flow.id) or {}
        after = model.apply_ops(before, ops)
        result = self.save(after, flow=flow.id, by=by, note=note or "changed " + model.describe_diff(model.diff(before, after)))
        if result["saved"]:
            result["changes"] = model.describe_diff(model.diff(before, after))
        return result

    def restore(self, ref, version: int, *, by: str = "user") -> dict:
        flow = self._need(ref)
        number = self.store.restore(flow.id, version, by=by)
        if number is None:
            raise LookupError(f"{flow.name} has no version {version}.")
        return {"restored": version, "version": number, "issues": self.check(self.store.doc(flow.id) or {})}

    def _need(self, ref):
        flow = self.store.get(ref)
        if flow is None:
            raise LookupError(f"There is no workflow {ref}.")
        return flow

    # ---- running -----------------------------------------------------------------------------------------------------
    def start(self, ref, *, mode: str = "real", input_=None, trigger_node: str | None = None, trigger: str = "manual", key: str = "") -> dict:
        flow = self._need(ref)
        if flow.archived:
            raise ValueError(f"{flow.name} is archived. Restore it first.")
        doc, version = self.store.doc(flow.id) or {}, flow.active_version
        errors = [i for i in self.check(doc) if i["level"] == "error"]
        run_id = self.store.start_run(flow.id, version, mode, trigger, input_ if input_ is not None else {}, key)
        if run_id is None:
            return {"duplicate": True, "status": "skipped"}
        if errors:  # truthful: it did not run, and says why
            reason = "It cannot run: " + "; ".join(i["message"] for i in errors[:3])
            self.store.finish_run(run_id, "blocked", error=reason)
            self._emit({"type": "flow", "run": run_id, "flow": flow.id, "node": "", "status": "blocked", "error": reason})
            return {"run": run_id, "status": "blocked", "error": reason}
        job = self._jobs.submit("flow", flow.name, self._work(flow.id, flow.name, run_id, version, mode, trigger_node, input_ if input_ is not None else {}, False),
                                budget_s=RUN_BUDGET_S, announce=self._announce, resumable=True,
                                payload={"flow": flow.id, "run": run_id, "version": version, "mode": mode, "trigger": trigger_node, "input": input_ or {}})
        self.store.set_job(run_id, job.id)
        return {"run": run_id, "job": job.id, "status": "running"}

    def resume(self, run_id: int) -> dict:
        run = self.store.run(run_id)
        if run is None or run["status"] not in ("interrupted", "failed", "blocked"):
            raise ValueError("Only a run that was interrupted, failed or is blocked can be resumed.")
        flow = self._need(run["flow_id"])
        if self.store.doc(flow.id, run["version"]) is None:
            raise LookupError("The version it ran on is gone.")
        job = self._jobs.submit("flow", flow.name, self._work(flow.id, flow.name, run_id, run["version"], run["mode"], None, run["input"] or {}, True),
                                budget_s=RUN_BUDGET_S, announce=self._announce, resumable=True,
                                payload={"flow": flow.id, "run": run_id, "version": run["version"], "mode": run["mode"], "input": run["input"] or {}})
        self.store.reopen_run(run_id, job.id)
        return {"run": run_id, "job": job.id, "status": "running", "resumed": True}

    def _resumer(self, job):
        p = job.payload or {}
        flow = self.store.get(p.get("flow"))
        return self._work(p.get("flow"), flow.name if flow else "?", int(p.get("run") or 0), int(p.get("version") or 0), p.get("mode") or "real", p.get("trigger"),
                          p.get("input") or {}, True)

    @staticmethod
    def _announce(job) -> str | None:
        return f"The workflow {job.goal} {'was blocked' if job.status == 'blocked' else 'failed'}: {job.result[:160]}" if job.status in BAD else None

    def _work(self, flow_id: int, name: str, run_id: int, version: int, mode: str, trigger_node, input_, resume: bool):
        async def work(job_ctx):
            doc = self.store.doc(flow_id, version)  # pinned: an edit during the run is for the next run
            if doc is None:
                self.store.finish_run(run_id, "failed", error="Its version is gone.")
                return JobOutcome("failed", "The version it ran on is gone.")
            if resume:
                self.store.reopen_run(run_id, job_ctx.job.id)
            env = self._env(job_ctx, name)
            try:
                res: RunResult = await execute(doc, self.store, run_id, env, mode=mode, trigger=trigger_node, trigger_input=input_, flow_id=flow_id, resume=resume)
            except asyncio.CancelledError:
                self.store.finish_run(run_id, "cancelled", error="Stopped.")
                self._emit({"type": "flow", "run": run_id, "flow": flow_id, "node": "", "status": "cancelled"})
                raise
            self.store.finish_run(run_id, res.status, res.output, res.error, res.verified, res.metrics)
            self._emit({"type": "flow", "run": run_id, "flow": flow_id, "node": "", "status": res.status, "error": res.error[:200], "verified": res.verified})
            return JobOutcome(res.status, self.summary(run_id))
        return work

    def _env(self, job_ctx, flow_name: str) -> Env:
        async def confirm(prompt: str) -> bool:
            adopted = await job_ctx.wait_on_open_approval(prompt, ttl_s=APPROVAL_WAIT_S)
            return adopted if adopted is not None else await job_ctx.ask_approval("flow_step", {"prompt": prompt}, prompt, risk=2, ttl_s=APPROVAL_TTL_S)
        worker = self._worker(f"workflow {flow_name}", confirm)
        origin = current_origin()

        def assess(name: str, args: dict):
            tool = None if str(name).startswith("flows_") else self._get_tool(name)
            return None if tool is None else classify(tool, args)

        async def run_child(ref, input_, depth, mode):
            return await self._child(job_ctx, ref, input_, depth, mode, worker, origin)
        return Env(call_tool=lambda name, args: worker.call(name, args), assess_tool=assess, ask_model=self._ask, notify=lambda text: self._notify(text),
                   run_child=run_child, emit=self._emit, clock=self._clock, waited=lambda: job_ctx.waited_s)

    async def _child(self, job_ctx, ref, input_, depth: int, mode: str, worker, origin) -> dict:
        flow = self.store.get(ref)
        if flow is None or flow.archived:
            return {"status": "blocked", "error": f"There is no workflow {ref}."}
        if depth > MAX_CHILD_DEPTH:
            return {"status": "failed", "error": f"Workflows are nested more than {MAX_CHILD_DEPTH} deep."}
        doc = self.store.doc(flow.id) or {}
        errors = [i for i in self.check(doc) if i["level"] == "error"]
        run_id = self.store.start_run(flow.id, flow.active_version, mode, "child", input_)
        if errors:
            self.store.finish_run(run_id, "blocked", error="; ".join(i["message"] for i in errors[:3]))
            return {"status": "blocked", "error": "; ".join(i["message"] for i in errors[:3])}
        env = self._env(job_ctx, flow.name)
        res = await execute(doc, self.store, run_id, env, mode=mode, trigger_input=input_, depth=depth, flow_id=flow.id)
        self.store.finish_run(run_id, res.status, res.output, res.error, res.verified, res.metrics)
        return {"status": res.status, "output": res.output, "error": res.error}

    # ---- what happened -----------------------------------------------------------------------------------------------
    def summary(self, run_id: int) -> str:
        run = self.store.run(run_id)
        if run is None:
            return "No such run."
        steps = self.store.steps(run_id)
        counts: dict[str, int] = {}
        for s in steps:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        how = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
        line = f"Run {run_id} ({run['mode']}) {run['status']}" + (" and verified" if run["verified"] else "") + f": {how}."
        if run["status"] in ("failed", "blocked", "interrupted"):
            d = diagnose(run, steps)
            line += f" {d['summary']}" + (f" {d.get('advice', '')}" if d.get("advice") else "")
        elif run["status"] == "done" and not run["verified"] and run["mode"] == "real":
            line += " Nothing checks the result."
        return line

    def run_detail(self, run_id: int) -> dict | None:
        run = self.store.run(run_id)
        if run is None:
            return None
        steps = self.store.steps(run_id)
        return {**run, "steps": steps, "diagnosis": diagnose(run, steps), "summary": self.summary(run_id)}

    async def wait(self, run_id: int, seconds: float = 30.0) -> dict | None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            run = self.store.run(run_id)
            if run is None or run["status"] != "running":
                break
            await asyncio.sleep(0.05)
        return self.run_detail(run_id)

    def describe(self, ref) -> dict:
        flow = self._need(ref)
        doc = self.store.doc(flow.id) or {}
        return {"id": flow.id, "name": flow.name, "creator": flow.creator, "enabled": flow.enabled, "archived": flow.archived, "version": flow.active_version,
                "doc": doc, "issues": self.check(doc), "stats": self.store.stats(flow.id), "versions": self.store.versions(flow.id)}

    # ---- triggers ----------------------------------------------------------------------------------------------------
    def tick(self, now: datetime | None = None) -> int:
        """Start the workflows whose schedule has come (called every minute). The same minute never starts the same workflow twice."""
        now = now or datetime.now()
        started = 0
        stamp = now.strftime("%Y-%m-%d %H:%M")
        for flow in self.store.list():
            if not flow.enabled:
                continue
            doc = self.store.doc(flow.id) or {}
            for n in doc.get("nodes", []):
                if n.get("type") != "trigger.schedule":
                    continue
                p = n.get("params") or {}
                days = [d.strip().lower() for d in str(p.get("days") or "").split(",") if d.strip()]
                if p.get("time") == now.strftime("%H:%M") and (not days or model.DAYS[now.weekday()] in days):
                    try:
                        r = self.start(flow.id, trigger_node=n["id"], trigger="schedule", key=f"schedule:{n['id']}:{stamp}", input_={"time": stamp})
                    except (ValueError, LookupError):
                        continue
                    started += 0 if r.get("duplicate") else 1
        return started

    def on_job_finished(self, job) -> None:
        """A job ended: start the workflows that wait for that. A workflow's own run never starts workflows, so none can chain forever."""
        if job.kind == "flow":
            return
        for flow in self.store.list():
            if not flow.enabled:
                continue
            for n in (self.store.doc(flow.id) or {}).get("nodes", []):
                p = n.get("params") or {}
                if n.get("type") == "trigger.job_done" and (not p.get("kind") or p["kind"] == job.kind) and (not p.get("status") or p["status"] in ("any", job.status)):
                    try:
                        self.start(flow.id, trigger_node=n["id"], trigger="job", key=f"job:{job.id}:{flow.id}:{n['id']}",
                                   input_={"id": job.id, "kind": job.kind, "status": job.status, "result": (job.result or "")[:500]})
                    except (ValueError, LookupError):
                        continue
