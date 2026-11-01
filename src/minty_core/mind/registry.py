"""What Minty can actually do right now, and how its own parts depend on each other: built from live metadata, never from a prompt.

Two views of the same facts:

* the **capability registry**: each plugin (a group of tools) with a status that is one of `available`, `needs configuration`, `unavailable right now` or, for a
  name nobody has, `does not exist`; plus reliability *evidence* (how many recorded calls, how many succeeded, how fast, the last error). Having a tool, or a key,
  is never counted as "works": a capability with no recorded calls says exactly that.
* the **digital twin**: components (plugins, background loops, runtime services, kits, providers, workflows) and the dependencies between them, taken from the code
  and the running app (which services a plugin or a loop reads is found in its source; which tools a workflow runs is in its definition), with the findings that
  follow: dependencies that are missing and so block several capabilities, tools that keep failing, parts nothing tests. A finding says whether it is verified (read
  from evidence) or a hypothesis (a guess from a proxy, such as test files that mention a plugin).
"""
from __future__ import annotations

import inspect
import json
import re
import shutil
from pathlib import Path

# what a plugin needs from the outside before it can work; checked against the real machine and the running app, never assumed
NEEDS = {
    "spotify": ("playerctl on this computer", lambda env: shutil.which("playerctl") is not None),
    "phone": ("Telegram switched on, a bot key and a paired chat", lambda env: "telegram" in env["services"]),
    "mcp": ("MCP switched on and a server set up", lambda env: bool(getattr(env["config"], "mcp", None) and env["config"].mcp.enabled and env["mcp_servers"])),
    "flows": ("the workflow service", lambda env: "flows" in env["services"]),
}
_HARD = re.compile(r"services\[\s*[\"']([a-z_]+)[\"']\s*\]")  # services["x"]: it fails without x
_SOFT = re.compile(r"services\.get\(\s*[\"']([a-z_]+)[\"']")  # services.get("x"): it copes without x


def evidence(config_dir: Path | None, days: int = 7) -> dict[str, dict]:
    """Per tool, from the trace files of the last days: {calls, ok, avg_ms, last_error}. Empty when there are no traces: absence of evidence is reported as such."""
    out: dict[str, dict] = {}
    if config_dir is None:
        return out
    folder = Path(config_dir) / "traces"
    files = sorted(folder.glob("*.jsonl"))[-max(1, days):] if folder.is_dir() else []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if '"tool_call"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") != "tool_call":
                continue
            rec = out.setdefault(str(e.get("name") or ""), {"calls": 0, "ok": 0, "ms": 0.0, "last_error": ""})
            rec["calls"] += 1
            rec["ok"] += 1 if e.get("ok") else 0
            rec["ms"] += float(e.get("ms") or 0)
            if not e.get("ok") and e.get("error"):
                rec["last_error"] = str(e["error"])[:120]
    for rec in out.values():
        rec["avg_ms"] = round(rec.pop("ms") / max(1, rec["calls"]), 1)
    return out


def _env(host, services: dict, config, config_dir) -> dict:
    try:
        from minty_core.mcp.registry import McpRegistry

        servers = McpRegistry(Path(config_dir) / "mcp.json").servers() if config_dir is not None else {}
    except Exception:  # noqa: BLE001 — a damaged registry means no servers, and says so below
        servers = {}
    return {"services": services, "config": config, "mcp_servers": servers, "host": host}


def snapshot(host, services: dict, config, config_dir: Path | None = None, router_status: list[dict] | None = None) -> dict:
    """The registry: every plugin as a capability with its status and evidence."""
    env = _env(host, services, config, config_dir)
    proof = evidence(config_dir)
    caps = []
    for p in host.list_plugins():
        need = NEEDS.get(p["name"])
        if p.get("unavailable"):
            status, why = "not available on this system", str(p["unavailable"])
        elif p["error"]:
            status, why = "unavailable right now", f"it failed to load: {str(p['error'])[:100]}"
        elif not p["enabled"]:
            status, why = "needs configuration", "it is switched off (the Switches page, or plugins.<name>.enabled)"
        elif need is not None and not need[1](env):
            status, why = "needs configuration", f"it needs {need[0]}"
        else:
            status, why = "available", ""
        calls = [(t, proof[t]) for t in p["tools"] if t in proof]
        total, ok = sum(r["calls"] for _, r in calls), sum(r["ok"] for _, r in calls)
        caps.append({"name": p["name"], "purpose": p["description"], "tools": p["tools"], "status": status, "why": why,
                     "evidence": ({"calls": total, "ok": ok, "failing": sorted(t for t, r in calls if r["calls"] >= 3 and r["ok"] / r["calls"] < 0.8)}
                                  if total else {"calls": 0, "note": "no recorded calls in the last days: loaded, not shown to work"})})
    providers = [{"name": s["name"], "enabled": s["enabled"], "has_key": s["has_key"], "health": s.get("health"), "cooling_for": s.get("cooling_for", 0)}
                 for s in (router_status or [])]
    counts: dict[str, int] = {}
    for c in caps:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {"capabilities": caps, "providers": providers, "counts": counts, "tools": sum(len(c["tools"]) for c in caps)}


def find(snap: dict, task: str) -> dict:
    """Which capabilities could do a task (by the words of their tools and purpose), and what stands in the way. A task nothing matches 'does not exist' here."""
    words = set(re.findall(r"[a-z]{4,}", task.lower())) - {"with", "that", "this", "from", "into", "have", "make", "please", "could", "would", "your"}
    hits = []
    for c in snap["capabilities"]:
        text = f"{c['name']} {c['purpose']} {' '.join(c['tools'])}".lower().replace("_", " ")
        score = sum(1 for w in words if w in text or w.rstrip("s") in text)
        if score:
            hits.append((score, c))
    hits.sort(key=lambda h: -h[0])
    found = [{"capability": c["name"], "status": c["status"], "why": c["why"], "tools": c["tools"][:6]} for _, c in hits[:5]]
    best = found[0] if found else None
    alternatives = [f["capability"] for f in found[1:] if f["status"] == "available"] if best and best["status"] != "available" else []
    return {"task": task, "status": best["status"] if best else "does not exist", "matches": found, "alternatives_that_are_available": alternatives,
            "note": ("the best match is " + best["capability"] + "; a match means the tool exists here, not that it has worked: see its evidence") if best else "nothing here does this: say so rather than pretend"}


# ---- the digital twin ------------------------------------------------------------------------------------------------
def _split(text: str) -> tuple[set[str], set[str]]:
    hard = set(_HARD.findall(text))
    return hard, set(_SOFT.findall(text)) - hard


def _services_read(fn) -> tuple[set[str], set[str]]:
    try:
        return _split(inspect.getsource(fn))
    except (OSError, TypeError):
        return set(), set()


def twin(host, services: dict, config, config_dir: Path | None = None, flows=None, router_status: list[dict] | None = None, tests_dir: Path | None = None) -> dict:
    """Components and the edges between them, from the code and the running app. Each edge says what it was derived from."""
    from minty_core import app as app_module
    from minty_core.plugins import builtin

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(key: str, kind: str, **more) -> None:
        nodes.setdefault(key, {"id": key, "kind": kind, **more})

    def edge(a: str, rel: str, b: str, source: str) -> None:
        edges.append({"from": a, "rel": rel, "to": b, "source": source})

    folder = Path(builtin.__file__).parent
    for p in host.list_plugins():
        node(f"plugin:{p['name']}", "plugin", enabled=p["enabled"], tools=len(p["tools"]), error=bool(p["error"]))
        try:
            hard, soft = _split((folder / p["name"] / "plugin.py").read_text(encoding="utf-8"))
            for svc in sorted(hard):
                edge(f"plugin:{p['name']}", "needs service", f"service:{svc}", "the plugin's source")
            for svc in sorted(soft):
                edge(f"plugin:{p['name']}", "may read service", f"service:{svc}", "the plugin's source")
        except OSError:
            pass
    for name in sorted(n for n in dir(app_module) if n.startswith("_") and n.endswith("_loop")):
        key = f"loop:{name.strip('_').removesuffix('_loop')}"
        node(key, "background loop")
        hard, soft = _services_read(getattr(app_module, name))
        for svc in sorted(hard):
            edge(key, "needs service", f"service:{svc}", "the loop's source")
        for svc in sorted(soft):
            edge(key, "may read service", f"service:{svc}", "the loop's source")
    for svc in services:
        node(f"service:{svc}", "service", running=True)
    for e in list(edges):
        if e["to"] not in nodes:
            node(e["to"], "service", running=False)
    for s in (router_status or []):
        node(f"provider:{s['name']}", "model provider", enabled=s["enabled"], health=s.get("health"))
    try:
        from minty_core.toolkits import KITS

        for kit, (plugins, _what, _kw) in KITS.items():
            node(f"kit:{kit}", "tool group")
            for plug in plugins:
                edge(f"kit:{kit}", "offers", f"plugin:{plug}", "the toolkit table")
    except ImportError:
        pass
    if flows is not None:
        for f in flows.store.list():
            node(f"flow:{f.name}", "workflow", enabled=f.enabled, version=f.active_version)
            for n in (flows.store.doc(f.id) or {}).get("nodes", []):
                p = n.get("params") or {}
                if n.get("type") == "tool.call" and p.get("tool"):
                    edge(f"flow:{f.name}", "runs tool", f"tool:{p['tool']}", "the workflow definition")
                if n.get("type") in ("flow.run", "flow.foreach") and p.get("flow"):
                    edge(f"flow:{f.name}", "runs workflow", f"flow:{p['flow']}", "the workflow definition")
    return {"components": list(nodes.values()), "edges": edges, "findings": diagnose(host, config_dir, nodes, edges, router_status, tests_dir)}


def impact(graph: dict, component: str) -> list[str]:
    """Everything that depends on a component, directly or through others: what a change to it could touch."""
    reverse: dict[str, set[str]] = {}
    for e in graph["edges"]:
        reverse.setdefault(e["to"], set()).add(e["from"])
    seen: set[str] = set()
    todo = [component]
    while todo:
        for dep in reverse.get(todo.pop(), ()):
            if dep not in seen:
                seen.add(dep)
                todo.append(dep)
    return sorted(seen)


def diagnose(host, config_dir, nodes: dict, edges: list[dict], router_status, tests_dir: Path | None) -> list[dict]:
    """What limits Minty now. `verified` = read from the running app or from recorded evidence; otherwise it is a hypothesis."""
    out: list[dict] = []
    missing: dict[str, list[str]] = {}
    for e in edges:
        if e["rel"] == "needs service" and not nodes.get(e["to"], {}).get("running"):
            missing.setdefault(e["to"], []).append(e["from"])
    for svc, readers in sorted(missing.items(), key=lambda kv: -len(set(kv[1]))):
        readers = sorted(set(readers))
        out.append({"kind": "missing dependency", "verified": True, "about": svc, "blocks": readers,
                    "summary": f"{svc.split(':')[1]} is not running, and {len(readers)} part(s) cannot work without it: {', '.join(r.split(':')[1] for r in readers[:5])}"})
    for p in host.list_plugins():
        if p["error"]:
            out.append({"kind": "plugin failed to load", "verified": True, "about": f"plugin:{p['name']}", "summary": f"{p['name']} failed to load: {str(p['error'])[:100]}"})
    for tool, rec in sorted(evidence(config_dir).items()):
        if rec["calls"] >= 5 and rec["ok"] / rec["calls"] < 0.7:
            out.append({"kind": "unreliable tool", "verified": True, "about": f"tool:{tool}",
                        "summary": f"{tool} succeeded {rec['ok']} of {rec['calls']} recorded calls; last error: {rec['last_error'] or 'none kept'}"})
    for s in (router_status or []):
        if s["enabled"] and (not s["has_key"] or s.get("cooling_for", 0) > 0 or s.get("health") not in (None, "ok", "up", "healthy")):
            out.append({"kind": "model provider trouble", "verified": True, "about": f"provider:{s['name']}",
                        "summary": f"{s['name']}: health {s.get('health')}, key {'yes' if s['has_key'] else 'no'}, cooling {s.get('cooling_for', 0)} s"})
    if tests_dir is not None and Path(tests_dir).is_dir():
        names = "\n".join(p.name.replace("_", "") for p in Path(tests_dir).glob("test_*.py"))
        for p in host.list_plugins():
            if p["name"].replace("_", "") not in names:
                out.append({"kind": "no test file mentions it", "verified": False, "about": f"plugin:{p['name']}",
                            "summary": f"no test file has {p['name']} in its name (a proxy only: it can still be tested from another file)"})
    return out
