"""The one definition of a workflow, and everything that reads it the same way: validation, linting, diffing, patching.

A workflow is plain JSON: nodes (id, type, params, pos), edges (from node and port, to node) and a name. The visual editor, the agent's tools, the
validator and the engine all use exactly this document, so there is nothing to drift. Every node type in the catalogue has a real implementation in
`engine.py`; a type that is not there cannot be saved. Conditions and templates are evaluated by a tiny safe evaluator over field paths (no eval, no
code): `{{input.x}}`, `{{nodes.fetch.items[0].title}}`, `{{prev.count}}`, `{{item.name}}`.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Callable

MAX_NODES = 60
MAX_DOC_CHARS = 200_000
ID = re.compile(r"[a-z][a-z0-9_]{0,29}")
TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_\-]+|\[\d+\])*)\s*\}\}")
HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists", "empty", "in", "matches")
ROOTS = {"input", "nodes", "prev", "item", "index"}
COMMON = {"retry": "obj", "timeout_s": "num", "note": "str", "label": "str"}


# ---- paths, templates and conditions (the whole expression language) -----------------------------------------------
def get_path(data, path: str):
    """`a.b[0].c` over dicts and lists; a missing step is None."""
    cur = data
    for part in re.findall(r"[A-Za-z0-9_\-]+|\[\d+\]", path):
        if part.startswith("["):
            i = int(part[1:-1])
            cur = cur[i] if isinstance(cur, list) and -len(cur) <= i < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def render(value, ctx: dict):
    """A string that is exactly one template gives the value itself (a list stays a list); text with templates gives text; dicts and lists are
    rendered inside; everything else is unchanged."""
    if isinstance(value, str):
        whole = TEMPLATE.fullmatch(value.strip())
        if whole:
            return get_path(ctx, whole.group(1))
        return TEMPLATE.sub(lambda m: _text(get_path(ctx, m.group(1))), value)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def _text(v) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate(cond, ctx: dict) -> bool:
    """{"path": "prev.score", "op": "gte", "value": 5} or {"all": [...]} / {"any": [...]} / {"not": c}."""
    if not isinstance(cond, dict):
        raise ValueError("a condition is an object")
    if "all" in cond:
        return all(evaluate(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, ctx) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], ctx)
    left, op = get_path(ctx, str(cond.get("path") or "")), str(cond.get("op") or "eq")
    right = render(cond.get("value"), ctx)
    if op == "exists":
        return left is not None
    if op == "empty":
        return left in (None, "", [], {})
    if op in ("eq", "ne"):
        same = left == right or (_num(left) is not None and _num(left) == _num(right) and not isinstance(left, bool))
        return same if op == "eq" else not same
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _num(left), _num(right)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op == "contains":
        if isinstance(left, (list, tuple)):
            return right in left
        return left is not None and str(right).lower() in str(left).lower()
    if op == "in":
        return isinstance(right, (list, tuple)) and left in right
    if op == "matches":
        try:
            return left is not None and re.search(str(right), str(left), re.IGNORECASE) is not None
        except re.error:
            return False
    raise ValueError(f"unknown operator {op}")


def condition_problem(cond) -> str:
    """'' when the condition is well formed."""
    if not isinstance(cond, dict):
        return "a condition has to be an object"
    for key in ("all", "any"):
        if key in cond:
            if not isinstance(cond[key], list) or not cond[key]:
                return f"{key} needs a list of conditions"
            for sub in cond[key]:
                if (p := condition_problem(sub)):
                    return p
            return ""
    if "not" in cond:
        return condition_problem(cond["not"])
    if not isinstance(cond.get("path"), str) or not cond["path"]:
        return "a condition needs a path"
    if cond.get("op", "eq") not in OPS:
        return f"unknown operator {cond.get('op')!r} (use one of {', '.join(OPS)})"
    return ""


# ---- the catalogue -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class NodeSpec:
    type: str
    title: str
    group: str
    doc: str
    params: dict  # name -> (kind, required)
    ports: tuple = ("out",)
    effect: str = "none"  # none | read | tool | model | notify | child: what running it can touch
    trigger: bool = False


def _spec(type_, title, group, doc, params=None, ports=("out",), effect="none", trigger=False):
    return NodeSpec(type_, title, group, doc, {**(params or {}), **{k: (v, False) for k, v in COMMON.items()}}, tuple(ports), effect, trigger)


CATALOGUE: dict[str, NodeSpec] = {s.type: s for s in (
    _spec("trigger.manual", "Manual start", "Triggers", "Starts when you or Minty run the workflow; its input is whatever the run is given.", trigger=True),
    _spec("trigger.schedule", "Schedule", "Triggers", "Starts at a time of day (HH:MM), on the days you name (mon,tue,... or empty for every day).",
          {"time": ("str", True), "days": ("str", False)}, trigger=True),
    _spec("trigger.job_done", "A job ended", "Triggers", "Starts when a background job ends. Its input is the job (id, kind, status, result).",
          {"kind": ("str", False), "status": ("str", False)}, trigger=True),
    _spec("data.set", "Set values", "Data", "Make a new object from templates: {name: '{{input.x}}'}.", {"values": ("obj", True)}),
    _spec("data.filter", "Filter list", "Data", "Keep the items of a list that meet a condition (the item is `item`).",
          {"list": ("any", True), "where": ("cond", True)}),
    _spec("data.map", "Map list", "Data", "Turn each item of a list into a new object (fields are templates over `item`).",
          {"list": ("any", True), "fields": ("obj", True)}),
    _spec("data.merge", "Merge", "Data", "Wait for the branches that arrive here and join their results.", {}),
    _spec("data.validate", "Validate", "Data", "Check that named fields are present; goes to `ok` or `invalid`.", {"required": ("list", True)}, ports=("ok", "invalid")),
    _spec("logic.if", "If", "Logic", "Branch on a condition: `true` or `false`.", {"where": ("cond", True)}, ports=("true", "false")),
    _spec("tool.call", "Run a tool", "Actions", "Run one of Minty's tools, through the same safety gate as everything else.",
          {"tool": ("str", True), "args": ("obj", False)}, effect="tool"),
    _spec("ai.ask", "Ask the model", "AI", "A bounded question to the model about some data. `choices` forces one of them; `fields` asks for those fields.",
          {"prompt": ("str", True), "data": ("any", False), "choices": ("list", False), "fields": ("list", False)}, effect="model"),
    _spec("flow.run", "Run a workflow", "Flow", "Run another workflow and use its result (the child's failure is this node's failure).",
          {"flow": ("str", True), "input": ("obj", False)}, effect="child"),
    _spec("flow.foreach", "For each item", "Flow", "Run another workflow once for each item of a list (at most 50).",
          {"list": ("any", True), "flow": ("str", True), "limit": ("num", False)}, effect="child"),
    _spec("flow.delay", "Wait", "Flow", "Wait a few seconds (at most 30).", {"seconds": ("num", True)}),
    _spec("out.notify", "Tell the user", "Output", "Say something to the user (through the notifier).", {"text": ("str", True)}, effect="notify"),
    _spec("out.result", "Result", "Output", "The workflow's result. `require` lists fields that must be filled for the result to count as verified.",
          {"value": ("any", True), "require": ("list", False)}),
)}


# ---- validation ----------------------------------------------------------------------------------------------------
def normalize(doc: dict) -> dict:
    """The document with every default filled; the input is not changed."""
    d = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    d["name"] = str(d.get("name") or "").strip()
    d["description"] = str(d.get("description") or "").strip()
    d["nodes"] = [{"id": n.get("id"), "type": n.get("type"), "params": n.get("params") if isinstance(n.get("params"), dict) else {},
                   "pos": n.get("pos") if isinstance(n.get("pos"), list) and len(n["pos"]) == 2 else [0, 0]}
                  for n in (d.get("nodes") if isinstance(d.get("nodes"), list) else []) if isinstance(n, dict)]
    d["edges"] = [{"from": e.get("from"), "port": e.get("port") or "out", "to": e.get("to")}
                  for e in (d.get("edges") if isinstance(d.get("edges"), list) else []) if isinstance(e, dict)]
    return d


def _refs(value) -> set[str]:
    """The node ids a params value points at through `{{nodes.<id>...}}`."""
    found: set[str] = set()
    if isinstance(value, str):
        for m in TEMPLATE.finditer(value):
            parts = m.group(1).split(".")
            if parts[0] == "nodes" and len(parts) > 1:
                found.add(re.split(r"[\[]", parts[1])[0])
    elif isinstance(value, dict):
        for v in value.values():
            found |= _refs(v)
    elif isinstance(value, list):
        for v in value:
            found |= _refs(v)
    return found


def _bad_roots(value) -> set[str]:
    bad: set[str] = set()
    if isinstance(value, str):
        bad |= {m.group(1).split(".")[0].split("[")[0] for m in TEMPLATE.finditer(value)} - ROOTS
    elif isinstance(value, dict):
        for v in value.values():
            bad |= _bad_roots(v)
    elif isinstance(value, list):
        for v in value:
            bad |= _bad_roots(v)
    return bad


def ancestors(edges: list[dict]) -> dict[str, set[str]]:
    parents: dict[str, set[str]] = {}
    for e in edges:
        parents.setdefault(e["to"], set()).add(e["from"])
    memo: dict[str, set[str]] = {}

    def up(n: str, seen=()) -> set[str]:
        if n in memo:
            return memo[n]
        out: set[str] = set()
        for p in parents.get(n, ()):
            if p not in seen:
                out |= {p} | up(p, seen + (n,))
        memo[n] = out
        return out
    return {n: up(n) for n in {e["to"] for e in edges} | {e["from"] for e in edges}}


def validate(doc: dict, *, tool_exists: Callable[[str], bool] | None = None, flow_exists: Callable[[str], bool] | None = None) -> list[dict]:
    """Every problem with a workflow, the same checks on save and on run. Items: {level: error|warn, node, message}. An empty error list means it can run."""
    issues: list[dict] = []

    def add(level: str, node: str | None, message: str) -> None:
        issues.append({"level": level, "node": node, "message": message})

    if not isinstance(doc, dict):
        return [{"level": "error", "node": None, "message": "A workflow is an object with nodes and edges."}]
    if len(json.dumps(doc, default=str)) > MAX_DOC_CHARS:
        return [{"level": "error", "node": None, "message": "The workflow is far too large."}]
    d = normalize(doc)
    if not d["name"]:
        add("error", None, "The workflow needs a name.")
    nodes = d["nodes"]
    if not nodes:
        add("error", None, "The workflow has no nodes.")
    if len(nodes) > MAX_NODES:
        add("error", None, f"Too many nodes ({len(nodes)}; at most {MAX_NODES}). Split it into child workflows.")
    ids: dict[str, dict] = {}
    for n in nodes:
        nid = n["id"]
        if not isinstance(nid, str) or not ID.fullmatch(nid):
            add("error", str(nid), f"Node id {nid!r} is not valid (lowercase letters, digits and _, starting with a letter).")
            continue
        if nid in ids:
            add("error", nid, f"Two nodes are called {nid}.")
            continue
        ids[nid] = n
        spec = CATALOGUE.get(n["type"])
        if spec is None:
            add("error", nid, f"{n['type']!r} is not a node type that exists. See the catalogue.")
            continue
        for name, (kind, required) in spec.params.items():
            if required and n["params"].get(name) in (None, "", [], {}):
                add("error", nid, f"{spec.title} needs {name}.")
        for name, value in n["params"].items():
            if name not in spec.params:
                add("error", nid, f"{spec.title} has no setting called {name}.")
                continue
            kind = spec.params[name][0]
            if kind == "cond" and (p := condition_problem(value)):
                add("error", nid, f"{name}: {p}.")
            elif kind == "obj" and value is not None and not isinstance(value, dict):
                add("error", nid, f"{name} has to be an object.")
            elif kind == "list" and value is not None and not isinstance(value, list):
                add("error", nid, f"{name} has to be a list.")
            elif kind == "num" and value is not None and (isinstance(value, bool) or _num(value) is None):
                add("error", nid, f"{name} has to be a number.")
            elif kind == "str" and value is not None and not isinstance(value, str):
                add("error", nid, f"{name} has to be text.")
            if (bad := _bad_roots(value)):
                add("error", nid, f"{name} refers to {', '.join(sorted(bad))}: use input, nodes.<id>, prev, item or index.")
        p = n["params"]
        if n["type"] == "trigger.schedule":
            if isinstance(p.get("time"), str) and not HHMM.fullmatch(p["time"]):
                add("error", nid, "The schedule time has to look like 07:30.")
            for day in [x.strip().lower() for x in str(p.get("days") or "").split(",") if x.strip()]:
                if day not in DAYS:
                    add("error", nid, f"{day!r} is not a day (use {', '.join(DAYS)}).")
        if n["type"] == "tool.call" and isinstance(p.get("tool"), str) and tool_exists is not None and not tool_exists(p["tool"]):
            add("error", nid, f"There is no tool called {p['tool']} here, so this step cannot run.")
        if n["type"] in ("flow.run", "flow.foreach") and isinstance(p.get("flow"), str) and flow_exists is not None and not flow_exists(p["flow"]):
            add("error", nid, f"There is no workflow called {p['flow']}.")
        if n["type"] == "flow.delay" and (_num(p.get("seconds")) or 0) > 30:
            add("error", nid, "A wait is at most 30 seconds.")
        if n["type"] == "flow.foreach" and (_num(p.get("limit")) or 0) > 50:
            add("error", nid, "For each takes at most 50 items.")
        r = p.get("retry")
        if r is not None and (not isinstance(r, dict) or not 0 <= (_num(r.get("max")) or 0) <= 3):
            add("error", nid, "retry is {max: 0-3, delay_s}.")
        if _num(p.get("timeout_s")) is not None and not 1 <= _num(p["timeout_s"]) <= 300:
            add("error", nid, "timeout_s is between 1 and 300 seconds.")
    for e in d["edges"]:
        a, b, port = e["from"], e["to"], e["port"]
        if a not in ids or b not in ids:
            add("error", None, f"A connection {a} -> {b} names a node that is not there.")
            continue
        sa, sb = CATALOGUE.get(ids[a]["type"]), CATALOGUE.get(ids[b]["type"])
        if sa is not None and (port not in sa.ports + ("error",) or (sa.trigger and port == "error")):
            add("error", a, f"{sa.title} has no output called {port} (it has {', '.join(sa.ports)}).")
        if sb is not None and sb.trigger:
            add("error", b, f"{sb.title} starts a workflow; nothing can lead into it.")
        if a == b:
            add("error", a, "A node cannot lead into itself.")
    if not any(CATALOGUE.get(n["type"]) and CATALOGUE[n["type"]].trigger for n in nodes):
        add("error", None, "The workflow needs a trigger (for example Manual start).")
    # no cycles: a loop is a For each over a child workflow, which has its own limit
    graph: dict[str, list[str]] = {}
    for e in d["edges"]:
        if e["from"] in ids and e["to"] in ids:
            graph.setdefault(e["from"], []).append(e["to"])
    state: dict[str, int] = {}

    def cyc(n: str) -> bool:
        state[n] = 1
        for m in graph.get(n, ()):
            if state.get(m) == 1 or (state.get(m) is None and cyc(m)):
                return True
        state[n] = 2
        return False
    if any(state.get(n) is None and cyc(n) for n in list(ids)):
        add("error", None, "The connections go round in a circle. Use For each over a child workflow to repeat work.")
    else:
        up = ancestors(d["edges"])
        reach: set[str] = set()
        frontier = [n["id"] for n in nodes if CATALOGUE.get(n["type"]) and CATALOGUE[n["type"]].trigger and n["id"] in ids]
        while frontier:
            cur = frontier.pop()
            if cur not in reach:
                reach.add(cur)
                frontier += graph.get(cur, [])
        for nid in ids:
            if nid not in reach and not (CATALOGUE.get(ids[nid]["type"]) and CATALOGUE[ids[nid]["type"]].trigger):
                add("warn", nid, "Nothing leads to this node, so it will never run.")
            for ref in _refs(ids[nid]["params"]):
                if ref not in ids:
                    add("error", nid, f"It uses the result of {ref}, which is not in the workflow.")
                elif ref not in up.get(nid, set()):
                    add("warn", nid, f"It uses the result of {ref}, which does not come before it, so it may be empty.")
    return issues


def lint(doc: dict) -> list[dict]:
    """Suggestions that are not errors: repeated work, missing error handling, results nobody uses."""
    d, hints = normalize(doc), []
    seen: dict[str, str] = {}
    used = set().union(*[_refs(n["params"]) for n in d["nodes"]]) if d["nodes"] else set()
    with_error = {e["from"] for e in d["edges"] if e["port"] == "error"}
    has_out = {e["from"] for e in d["edges"]}
    for n in d["nodes"]:
        nid, p = n["id"], n["params"]
        if n["type"] == "tool.call":
            key = json.dumps([p.get("tool"), p.get("args")], sort_keys=True, default=str)
            if key in seen:
                hints.append({"level": "hint", "node": nid, "message": f"The same tool call as {seen[key]}: one of them is redundant (use its result)."})
            seen[key] = nid
            if nid not in with_error and not p.get("retry"):
                hints.append({"level": "hint", "node": nid, "message": "No error path and no retry: a failure stops the whole run."})
        if n["type"] == "ai.ask" and p.get("choices") and not p.get("data"):
            hints.append({"level": "hint", "node": nid, "message": "A model call with no data to look at: a condition may do the same without a model."})
        if n["type"] in ("data.set", "data.map", "data.filter") and nid not in used and nid not in has_out:
            hints.append({"level": "hint", "node": nid, "message": "Its result is not used by anything."})
    return hints


# ---- diff and patch ------------------------------------------------------------------------------------------------
def diff(a: dict, b: dict) -> dict:
    A, B = normalize(a), normalize(b)
    na, nb = {n["id"]: n for n in A["nodes"]}, {n["id"]: n for n in B["nodes"]}
    ea = {(e["from"], e["port"], e["to"]) for e in A["edges"]}
    eb = {(e["from"], e["port"], e["to"]) for e in B["edges"]}
    return {"added_nodes": sorted(set(nb) - set(na)), "removed_nodes": sorted(set(na) - set(nb)),
            "changed_nodes": sorted(i for i in set(na) & set(nb) if (na[i]["type"], na[i]["params"]) != (nb[i]["type"], nb[i]["params"])),
            "added_edges": sorted(f"{x}.{p} -> {y}" for x, p, y in eb - ea), "removed_edges": sorted(f"{x}.{p} -> {y}" for x, p, y in ea - eb),
            "renamed": A["name"] != B["name"]}


def describe_diff(d: dict) -> str:
    parts = []
    for key, label in (("added_nodes", "added"), ("removed_nodes", "removed"), ("changed_nodes", "changed")):
        if d[key]:
            parts.append(f"{label}: {', '.join(d[key])}")
    if d["added_edges"] or d["removed_edges"]:
        parts.append(f"connections +{len(d['added_edges'])} -{len(d['removed_edges'])}")
    if d["renamed"]:
        parts.append("renamed")
    return "; ".join(parts) or "no change"


def apply_ops(doc: dict, ops: list) -> dict:
    """Patch a workflow: add_node, remove_node, set_params (with unset), connect, disconnect, move, rename. A new document comes back; nothing is saved here."""
    d = normalize(doc)
    if not isinstance(ops, list) or not ops:
        raise ValueError("give a list of changes")
    for op in ops:
        if not isinstance(op, dict):
            raise ValueError("each change is an object")
        kind = op.get("op")
        node = next((n for n in d["nodes"] if n["id"] == op.get("id")), None)
        if kind == "add_node":
            if node is not None:
                raise ValueError(f"there is already a node {op.get('id')}")
            d["nodes"].append({"id": op.get("id"), "type": op.get("type"), "params": dict(op.get("params") or {}), "pos": op.get("pos") or [0, 0]})
        elif kind == "remove_node":
            if node is None:
                raise ValueError(f"there is no node {op.get('id')}")
            d["nodes"].remove(node)
            d["edges"] = [e for e in d["edges"] if op["id"] not in (e["from"], e["to"])]
        elif kind == "set_params":
            if node is None:
                raise ValueError(f"there is no node {op.get('id')}")
            node["params"].update(op.get("params") or {})
            for name in op.get("unset") or []:
                node["params"].pop(name, None)
        elif kind == "move":
            if node is None:
                raise ValueError(f"there is no node {op.get('id')}")
            node["pos"] = op.get("pos") or node["pos"]
        elif kind == "connect":
            e = {"from": op.get("from"), "port": op.get("port") or "out", "to": op.get("to")}
            if e not in d["edges"]:
                d["edges"].append(e)
        elif kind == "disconnect":
            d["edges"] = [e for e in d["edges"] if not (e["from"] == op.get("from") and e["to"] == op.get("to") and (not op.get("port") or e["port"] == op["port"]))]
        elif kind == "rename":
            d["name"] = str(op.get("name") or d["name"])
            d["description"] = str(op.get("description") if op.get("description") is not None else d["description"])
        else:
            raise ValueError(f"unknown change {kind!r} (add_node, remove_node, set_params, connect, disconnect, move, rename)")
    return d
