"""Minty's hands on its model of the owner's world: record what it learns with its source, recall what is known about something, and keep the decisions and why.

Called from the world store (`ctx.services["world"]`). Two rules are enforced here, not left to the model: a turn that read outside content (mail, a page, a file) cannot record
anything as `told` or `verified` or as a decision the owner made, and a turn that is private marks what it records private.
"""
import json

from minty_core.mind.world import DECISION_KINDS, KINDS, LINKS
from minty_core.plugins.host import Tool
from minty_core.policy import R1


def register(ctx):
    def store():
        w = ctx.services.get("world")
        if w is None:
            raise RuntimeError("the world model is not running here")
        return w

    def outside() -> bool:
        return bool(ctx.services.get("turn_read_outside", lambda: False)())

    def private() -> bool:
        return bool(ctx.services.get("is_private", lambda: False)())

    def out(value) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    def guarded(fn):
        async def run(args):
            try:
                return await fn(args)
            except (ValueError, LookupError, RuntimeError, KeyError) as e:
                return f"Not done: {e}"
        return run

    async def note(args):
        w, o, p = store(), outside(), private()
        kind, name, prov, src = str(args.get("kind") or "other"), str(args.get("name") or ""), str(args.get("provenance") or "inferred"), str(args.get("source") or "")
        if args.get("rel"):
            r = w.relate(kind, name, str(args["rel"]), str(args.get("to_kind") or "other"), str(args.get("to_name") or ""), prov, src, outside=o, private=p)
            return out({**r, "note": "a link, with its source"})
        if not args.get("attr") or args.get("value") in (None, ""):
            raise ValueError("give attr and value (a fact), or rel and to_name (a link)")
        r = w.note(kind, name, str(args["attr"]), str(args["value"]), prov, src, outside=o, private=p)
        if o and prov in ("told", "verified"):
            r["downgraded"] = "this turn read outside content, so it is recorded as discovered, not as something the owner said"
        if r.get("conflicts", 0) > 1 or r["status"] == "conflict":
            r["attention"] = "this disagrees with something already known: both are kept, and the owner should say which is right"
        return out(r)

    async def recall(args):
        w, p = store(), private()
        if args.get("impact"):
            return out(w.impact(str(args["impact"])))
        if args.get("decisions"):
            return out({"decisions": w.decisions(str(args.get("query") or ""), include_private=p), "wrong_assumptions": w.wrong_assumptions()[:5]})
        data = w.recall(str(args.get("query") or ""), include_private=p)
        if args.get("conflicts"):
            data["conflicts"] = w.conflicts()
        return out(data)

    async def decide(args):
        w, o, p = store(), outside(), private()
        action = str(args.get("action") or "record")
        if action == "outcome":
            return out({"ok": w.outcome(int(args["id"]), str(args.get("outcome") or ""), str(args.get("result") or ""))})
        kind = str(args.get("kind") or "decided")
        did = w.decide(str(args.get("title") or ""), kind, str(args.get("rationale") or ""), str(args.get("alternatives") or ""), str(args.get("assumptions") or ""),
                       str(args.get("expected") or ""), str(args.get("source") or ""), args.get("about") or [], outside=o, private=p,
                       supersedes=int(args["revises"]) if action == "revise" and args.get("revises") is not None else None)
        return out({"id": did, "recorded_as": "proposed" if o and kind == "decided" else kind,
                    "note": "the earlier decision stays on record as superseded" if action == "revise" else "only what the owner decided is a decision; the rest is labelled"})

    s, i = {"type": "string"}, {"type": "integer"}
    return [
        Tool("note", "Record what you learned with its source: a fact (kind, name, attr, value) or a link (rel, to_name). provenance: told (owner said it), discovered, observed, inferred, assumed.",
             {"type": "object", "properties": {"kind": {"type": "string", "enum": list(KINDS)}, "name": s, "attr": s, "value": s, "rel": {"type": "string", "enum": list(LINKS)},
                                               "to_kind": s, "to_name": s, "provenance": s, "source": s}, "required": ["name"]}, guarded(note), risk=R1),
        Tool("recall", "What is known about things named in query (facts with sources, conflicts, links, decisions); impact=name: what a change may affect; decisions=true: past decisions.",
             {"type": "object", "properties": {"query": s, "impact": s, "decisions": {"type": "boolean"}, "conflicts": {"type": "boolean"}}}, guarded(recall), sensitive=True, risk=R1),
        Tool("decide", "Record a decision and why (action record), revise one (revises=id; the old stays), or its outcome (id, outcome, result good/bad/mixed/wrong). kind: " + "/".join(DECISION_KINDS) + ".",
             {"type": "object", "properties": {"action": s, "title": s, "kind": s, "rationale": s, "alternatives": s, "assumptions": s, "expected": s, "source": s,
                                               "about": {"type": "array", "items": s}, "revises": i, "id": i, "outcome": s, "result": s}}, guarded(decide), risk=R1),
    ]
