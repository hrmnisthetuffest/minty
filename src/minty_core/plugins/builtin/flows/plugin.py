"""The agent's hands on the workflow system: the very same service the panel's editor uses (`ctx.services["flows"]`).

There is no owner check: a workflow made by hand in the panel is read, changed, tested and run exactly like one Minty made. What keeps that responsible is that
nothing is lost (every edit is a new version, going back is one call, deleting is archiving), every definition is validated before it is saved, a test run only
assesses what would change something, and each tool step of a real run still goes through the safety gate.
"""
import json

from minty_core.flows import model
from minty_core.plugins.host import Tool
from minty_core.policy import R1, R2, Mandate

LIMIT = 12000
# turning a workflow on makes it run by itself later (a schedule, an event): asked, unless the user's own words asked for that
ENABLE = Mandate(r"\b(enable|turn on|switch on|activate|schedule|automat\w*|every (?:day|morning|evening|week|hour)|each (?:day|morning|week))\b", max_class=R2)


def register(ctx):
    def svc():
        s = ctx.services.get("flows")
        if s is None:
            raise RuntimeError("Workflows are not running here.")
        return s

    def out(value) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text if len(text) <= LIMIT else text[:LIMIT] + "… (cut)"

    def guarded(fn):
        async def run(args):
            try:
                return await fn(args)
            except (ValueError, LookupError, RuntimeError, KeyError) as e:
                return f"Not done: {e}"
        return run

    def issues_text(issues: list) -> list:
        return [f"{i['level']}{' ' + i['node'] if i.get('node') else ''}: {i['message']}" for i in issues]

    async def catalogue(args):
        return out(svc().catalogue())

    async def list_(args):
        s = svc()
        return out([{"id": f.id, "name": f.name, "creator": f.creator, "enabled": f.enabled, "archived": f.archived, "version": f.active_version,
                     "last": next(iter([r["status"] for r in s.store.runs(f.id, 1)]), None)} for f in s.store.list(include_archived=bool(args.get("archived")))])

    async def get(args):
        return out(svc().describe(args["flow"]))

    async def check(args):
        s = svc()
        doc = args.get("doc") if isinstance(args.get("doc"), dict) else (s.store.doc(s._need(args.get("flow")).id) if args.get("flow") is not None else None)
        if doc is None:
            raise ValueError("give a flow or a doc")
        issues = s.check(doc)
        return out({"can_run": not any(i["level"] == "error" for i in issues), "issues": issues_text(issues)})

    async def save(args):
        r = svc().save(args["doc"], flow=args.get("flow"), by="minty", note=str(args.get("note") or ""))
        return out({**r, "issues": issues_text(r["issues"])})

    async def patch(args):
        r = svc().patch(args["flow"], args["ops"], by="minty", note=str(args.get("note") or ""))
        return out({**r, "issues": issues_text(r["issues"])})

    async def run(args):
        s = svc()
        started = s.resume(int(args["resume"])) if args.get("resume") else s.start(args["flow"], mode="test" if args.get("test") else "real", input_=args.get("input"))
        if started.get("status") != "running" or args.get("wait") is False:
            return out(started)
        detail = await s.wait(started["run"], 40.0)
        return out({"run": detail["id"], "status": detail["status"], "verified": detail["verified"], "summary": detail["summary"], "output": detail["output"],
                    "steps": [{k: v for k, v in {"node": x["node"], "status": x["status"], "ms": x["ms"], "error": x["error"]}.items() if v not in (None, "")} for x in detail["steps"]]})

    async def runs(args):
        s = svc()
        if args.get("run") is not None:
            detail = s.run_detail(int(args["run"]))
            if detail is None:
                raise LookupError(f"There is no run {args['run']}.")
            return out(detail)
        ref = s._need(args["flow"]).id if args.get("flow") is not None else None
        return out({"runs": [{"id": r["id"], "flow": r["flow_id"], "version": r["version"], "mode": r["mode"], "trigger": r["trigger"], "status": r["status"],
                              "verified": r["verified"], "error": r["error"][:120]} for r in s.store.runs(ref, int(args.get("limit") or 10))],
                    "stats": s.store.stats(ref) if ref is not None else None})

    async def history(args):
        s = svc()
        flow = s._need(args["flow"])
        if args.get("a") is None:
            return out(s.store.versions(flow.id))
        a, b = s.store.doc(flow.id, int(args["a"])), s.store.doc(flow.id, int(args.get("b") or flow.active_version))
        if a is None or b is None:
            raise LookupError("One of those versions does not exist.")
        return out({"diff": model.diff(a, b), "summary": model.describe_diff(model.diff(a, b))})

    async def restore(args):
        r = svc().restore(args["flow"], int(args["version"]), by="minty")
        return out({**r, "issues": issues_text(r["issues"])})

    async def set_(args):
        s = svc()
        flow = s._need(args["flow"])
        if args.get("archived") is not None:
            s.store.set_archived(flow.id, bool(args["archived"]))
        if args.get("enabled") is not None:
            if args["enabled"] and any(i["level"] == "error" for i in s.check(s.store.doc(flow.id) or {})):
                raise ValueError("It has errors, so it cannot be switched on (flows_check shows them).")
            s.store.set_enabled(flow.id, bool(args["enabled"]))
        f = s.store.get(flow.id)
        return out({"id": f.id, "name": f.name, "enabled": f.enabled, "archived": f.archived})

    def enable_risk(args):
        return R2 if args.get("enabled") else R1

    s, o, b, i = {"type": "string"}, {"type": "object"}, {"type": "boolean"}, {"type": "integer"}
    ref = {"flow": {"type": ["string", "integer"]}}
    return [
        Tool("catalogue", "Node types you can build with, the tools a step can run, and the expression syntax.", {"type": "object", "properties": {}}, guarded(catalogue), risk=R1),
        Tool("list", "The workflows (id, name, who made it, on or off, last run).", {"type": "object", "properties": {"archived": b}}, guarded(list_), risk=R1),
        Tool("get", "One workflow: its nodes and connections, problems, run stats and versions. Any workflow, whoever made it.",
             {"type": "object", "properties": ref, "required": ["flow"]}, guarded(get), risk=R1),
        Tool("check", "Validate a workflow (flow) or a draft (doc) without saving: errors, warnings, hints.", {"type": "object", "properties": {**ref, "doc": o}}, guarded(check), risk=R1),
        Tool("save", "Create a workflow, or with flow replace one with a new version. doc = {name, nodes:[{id,type,params,pos}], edges:[{from,port,to}]}. Saved only if valid.",
             {"type": "object", "properties": {"doc": o, **ref, "note": s}, "required": ["doc"]}, guarded(save), risk=R1),
        Tool("patch", "Change a workflow in place as a new version. ops: add_node, remove_node, set_params, connect, disconnect, move, rename.",
             {"type": "object", "properties": {**ref, "ops": {"type": "array", "items": o}, "note": s}, "required": ["flow", "ops"]}, guarded(patch), risk=R1),
        Tool("run", "Run a workflow: test=true only assesses what would change something. Give input, or resume a run. Returns the result and per-step status.",
             {"type": "object", "properties": {**ref, "test": b, "input": o, "wait": b, "resume": i}}, guarded(run), sensitive=True, risk=R1),
        Tool("runs", "A run in detail with its steps and diagnosis (run=id), or the recent runs and stats of a workflow.",
             {"type": "object", "properties": {**ref, "run": i, "limit": i}}, guarded(runs), sensitive=True, risk=R1),
        Tool("history", "A workflow's versions, or what changed between versions a and b.", {"type": "object", "properties": {**ref, "a": i, "b": i}, "required": ["flow"]}, guarded(history), risk=R1),
        Tool("restore", "Go back to an earlier version (as a new version; nothing is lost).", {"type": "object", "properties": {**ref, "version": i}, "required": ["flow", "version"]}, guarded(restore), risk=R1),
        Tool("set", "Turn a workflow on or off (enabled) or archive/unarchive it. Never deletes.", {"type": "object", "properties": {**ref, "enabled": b, "archived": b}, "required": ["flow"]},
             guarded(set_), risk=enable_risk, mandate=ENABLE),
    ]
