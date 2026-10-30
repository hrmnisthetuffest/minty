"""The knowledge universe: one map over everything Minty knows, built from the real records at the moment you ask.

Nothing is stored for the picture. Nodes are the second brain's items and the originals they rest on, the world model's people, organisations, projects and decisions, the notes
folder's notes when it is on, and missions, workflows or playbooks once something links to them. Edges are the typed links (confirmed, or merely suggested and drawn differently),
the world model's relations, the notes' own links, and "derived from" for the evidence behind each item. A node's size is how many confirmed connections it has, its glow how recently
it changed, and its ring who wrote it. Private records, rejected links and archived items are not in it unless the owner asks for private ones. A cluster is found by letting each node
take the label most of its neighbours have (projects seed the labels), so what belongs together drifts together without anyone sorting it.
"""
from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from datetime import datetime

from minty_core.knowledge.reason import OUTSIDE_NOTE
from minty_core.knowledge.store import parse_ref

GLOW_DAYS = 7.0
_WORLD_BOOST = 2.0


def _short(text, n: int = 140) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def _ts(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def _vault_rows(services: dict) -> list[dict]:
    provider = services.get("vault")
    try:
        if provider is None or not provider.active():
            return []
        return list(provider.kit().vault.index.rows(None, limit=600))
    except Exception:  # noqa: BLE001 — a notes folder that cannot be read is simply not in the picture
        return []


def _vault_links(services: dict) -> list[tuple[str, str]]:
    try:
        return list(services["vault"].kit().vault.index.link_pairs())
    except Exception:  # noqa: BLE001
        return []


def build(services: dict, include_private: bool = False, max_nodes: int = 900, focus: str = "", now: float | None = None) -> dict:
    now = now or time.time()
    kb, world = services["knowledge"], services.get("world")
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    def glow(ts: float) -> float:
        return round(max(0.0, 1.0 - (now - ts) / (GLOW_DAYS * 86400.0)), 3) if ts else 0.0

    def add(ref: str, label: str, kind: str, system: str, **extra) -> dict:
        nodes[ref] = {"id": ref, "label": _short(label, 80), "kind": kind, "system": system, "authored": "", "status": "", "project": "", "snippet": "", "private": False,
                      "evidence": 0, "updated": 0.0, "degree": 0, **extra}
        nodes[ref]["glow"] = glow(nodes[ref]["updated"])
        return nodes[ref]

    def edge(a: str, rel: str, b: str, status: str = "confirmed", **extra) -> None:
        if a in nodes and b in nodes and a != b and (a, rel, b) not in edges:
            edges[(a, rel, b)] = {"a": a, "b": b, "rel": rel, "status": status, "by": "", "why": "", "score": 0.0, "id": None, **extra}

    # ---- the second brain's own items and originals --------------------------------------------------------------------
    items = kb.items(include_private=include_private)
    for it in items:
        ev = kb.evidence(it["id"])
        add(f"item:{it['id']}", it["title"], it["kind"], "knowledge", authored=it["authored"], status=it["status"], project=it["project"], snippet=_short(it["summary"] or it["body"] or ""),
            private=it["private"], evidence=len(ev), updated=it["updated"], created=it["created"])
    for it in items:
        for ev in kb.evidence(it["id"]):
            sref = f"source:{ev['source']}"
            if sref not in nodes:
                src = kb.source(ev["source"])
                if src is None or (src["private"] and not include_private):
                    continue
                add(sref, src["title"], "source", "knowledge", authored=src["origin"], snippet=_short(src["text"]), updated=src["captured"], created=src["captured"], sub=src["kind"], private=src["private"], uri=src["uri"])
            edge(f"item:{it['id']}", "derived from", sref)

    # ---- the world model: people, organisations, projects, decisions -----------------------------------------------------
    by_name: dict[str, str] = {}
    if world is not None:
        wg = world.graph(include_private=include_private)
        for e in wg["entities"]:
            ref = f"world:{e['name']}"
            by_name[e["name"].lower()] = ref
            add(ref, e["name"], e["kind"], "world", snippet=_short("; ".join(e["facts"])), private=e["private"], updated=e["updated"], created=e["updated"])
        for r in wg["relations"]:
            edge(f"world:{r['a']}", r["rel"], f"world:{r['b']}")
        for d in wg["decisions"]:
            ref = f"decision:{d['id']}"
            add(ref, d["title"], "decision", "world", status=d["kind"], snippet=_short(d["rationale"]), updated=d["ts"], created=d["ts"])
            for name in d["entities"]:
                edge(ref, "about", f"world:{name}")

    # ---- the notes folder, when it is on -----------------------------------------------------------------------------------
    for row in _vault_rows(services):
        authored = "outside" if row.get("confidence") == "untrusted" else "you" if row.get("confidence") == "verified" else "minty"
        add(f"vault:{row['id']}", row["title"], "note", "vault", authored=authored, project=row.get("project", ""), snippet=_short(", ".join(row.get("tags", []))), updated=_ts(row.get("updated")))
    for a, b in _vault_links(services):
        edge(f"vault:{a}", "links to", f"vault:{b}")

    # ---- the typed links between all of it, and the work they point at ---------------------------------------------------------
    jobs, flows = services.get("jobs"), services.get("flows")
    flow_names = {}
    if flows is not None:
        try:
            flow_names = {str(f.id): f.name for f in flows.store.list(include_archived=True)}
        except Exception:  # noqa: BLE001
            flow_names = {}

    def resolve(ref: str) -> str | None:
        system, ident = parse_ref(ref)
        if system == "world":
            return by_name.get(ident.lower())
        if ref in nodes:
            return ref
        if system == "mission" and jobs is not None and ident.isdigit() and jobs.get(int(ident)) is not None:
            job = jobs.get(int(ident))
            add(ref, job.goal, "mission", "work", status=job.status, updated=job.touched or job.started or job.created, snippet=_short(job.result))
            return ref
        if system == "flow" and ident in flow_names:
            add(ref, flow_names[ident], "flow", "work")
            return ref
        if system == "playbook":
            add(ref, ident, "playbook", "work")
            return ref
        return None

    for link in kb.links(statuses=("confirmed", "suggested")):
        try:
            a, b = resolve(link["src"]), resolve(link["dst"])
        except ValueError:
            continue
        if a and b:
            edge(a, link["rel"], b, link["status"], by=link["by"], why=link["why"], score=link["score"], id=link["id"])
    # a work node only a filtered-out link made visible is dropped again
    linked = {e for k in edges.values() for e in (k["a"], k["b"])}
    for ref in [r for r, n in nodes.items() if n["system"] == "work" and r not in linked]:
        del nodes[ref]

    # ---- how connected, then which to keep ------------------------------------------------------------------------------
    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in edges.values():
        w = 1.0 if e["status"] == "confirmed" else 0.3
        adjacency[e["a"]].append((e["b"], w))
        adjacency[e["b"]].append((e["a"], w))
        if e["status"] == "confirmed":
            nodes[e["a"]]["degree"] += 1
            nodes[e["b"]]["degree"] += 1
    total = len(nodes)
    if total > max_nodes:
        keep: set[str] = set()
        if focus in nodes:
            keep.add(focus)
            keep.update(n for n, _ in adjacency[focus][: max(0, max_nodes // 2)])
        rank = sorted(nodes, key=lambda r: (-(nodes[r]["degree"] * 2 + nodes[r]["glow"] + (_WORLD_BOOST if nodes[r]["system"] == "world" else 0.0)), r))
        for ref in rank:
            if len(keep) >= max_nodes:
                break
            keep.add(ref)
        nodes = {r: nodes[r] for r in keep}
        edges = {k: e for k, e in edges.items() if e["a"] in nodes and e["b"] in nodes}
    for n in nodes.values():
        n["size"] = round(1.0 + math.log2(1 + n["degree"]), 3)

    clusters = _clusters(nodes, edges)
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "clusters": clusters, "empty": not nodes, "truncated": total - len(nodes), "generated": now,
            "legend": dict(Counter(n["kind"] for n in nodes.values())), "stats": kb.stats(include_private)}


def _clusters(nodes: dict[str, dict], edges: dict) -> list[dict]:
    """Label propagation: each node takes the label most of its (confirmed, and a little of its suggested) neighbours carry; a project seeds one label for all of its members."""
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in edges.values():
        w = 1.0 if e["status"] == "confirmed" else 0.3
        adj[e["a"]].append((e["b"], w))
        adj[e["b"]].append((e["a"], w))
    label = {r: (f"project:{n['project']}" if n["project"] else r) for r, n in nodes.items()}
    order = sorted(nodes)
    for _ in range(10):
        changed = False
        for r in order:
            if not adj[r]:
                continue
            votes: Counter = Counter()
            for other, w in adj[r]:
                votes[label[other]] += w
            top = max(votes.values())
            best = min(lab for lab, v in votes.items() if v == top)
            if votes[best] > votes.get(label[r], 0) and best != label[r]:
                label[r] = best
                changed = True
        if not changed:
            break
    groups: dict[str, list[str]] = defaultdict(list)
    for r, lab in label.items():
        groups[lab].append(r)
    out, loose = [], []
    for n, (lab, members) in enumerate(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))):
        if len(members) < 2:
            loose += members
            continue
        cid = lab if lab.startswith("project:") else f"c{n}"
        name = lab[len("project:"):] if lab.startswith("project:") else nodes[max(members, key=lambda m: (nodes[m]["degree"], m))]["label"]
        for m in members:
            nodes[m]["cluster"] = cid
        out.append({"id": cid, "label": _short(name, 32), "size": len(members), "members": members})
    for m in loose:
        nodes[m]["cluster"] = "_loose"
    if loose:
        out.append({"id": "_loose", "label": "Unconnected", "size": len(loose), "members": loose})
    return out


def find(services: dict, query: str, include_private: bool = False, k: int = 12) -> list[dict]:
    """What a search should light up: items by words and spelling, originals by their text, people and organisations by name, notes by title."""
    kb, brain, world = services["knowledge"], services.get("knowledge_reason"), services.get("world")
    q = " ".join(str(query or "").split())
    if not q:
        return []
    hits: list[dict] = []
    if brain is not None:
        hits += [{"ref": h["ref"], "label": h["title"], "kind": h["kind"], "why": h["snippet"]} for h in brain.search(q, k, include_private)]
    for sid, _ in kb.search_sources(q, 5, include_private=include_private):
        src = kb.source(sid)
        if src:
            hits.append({"ref": f"source:{sid}", "label": src["title"], "kind": "source", "why": _short(src["text"], 100)})
    low = q.lower()
    if world is not None:
        for e in world.graph(include_private=include_private)["entities"]:
            if e["name"].lower() in low or low in e["name"].lower():
                hits.append({"ref": f"world:{e['name']}", "label": e["name"], "kind": e["kind"], "why": _short("; ".join(e["facts"]), 100)})
    words = [w for w in low.split() if len(w) >= 3]
    for row in _vault_rows(services):
        if words and any(w in row["title"].lower() or w in " ".join(row.get("tags", [])).lower() for w in words):
            hits.append({"ref": f"vault:{row['id']}", "label": row["title"], "kind": "note", "why": ""})
    seen, out = set(), []
    for h in hits:
        if h["ref"] not in seen:
            seen.add(h["ref"])
            out.append(h)
    return out[: k + 6]


def node(services: dict, ref: str, include_private: bool = False) -> dict | None:
    """One thing opened: the content (the original apart from any summary), who wrote it, how far it is checked, the evidence, every connection and the history."""
    kb, brain, world = services["knowledge"], services.get("knowledge_reason"), services.get("world")
    try:
        system, ident = parse_ref(ref)
    except ValueError:
        return None

    def connections(r: str) -> list[dict]:
        out = []
        for x in kb.links(r, statuses=("confirmed", "suggested")):
            out_dir = x["src"] == r
            other = x["dst"] if out_dir else x["src"]
            out.append({"id": x["id"], "rel": x["rel"], "direction": "out" if out_dir else "in", "ref": other, "label": brain._label(other) if brain else other, "status": x["status"],
                        "by": x["by"], "why": x["why"], "score": x["score"]})
        return out

    if system == "item":
        it = kb.item(ident)
        if it is None or (it["private"] and not include_private):
            return None
        ev = kb.evidence(ident)
        first = kb.source(ev[0]["source"]) if ev else None
        return {**it, "ref": ref, "system": "knowledge", "evidence": ev, "links": connections(ref), "history": kb.history(ref, 12), "related": brain.related_to_item(ident)[:4] if brain else [],
                "original": {"id": first["id"], "kind": first["kind"], "title": first["title"], "uri": first["uri"], "origin": first["origin"], "text": first["text"][:4000]} if first else None,
                "note": OUTSIDE_NOTE if it["authored"] == "outside" else ""}
    if system == "source":
        s = kb.source(ident)
        if s is None or (s["private"] and not include_private):
            return None
        return {"ref": ref, "system": "knowledge", "kind": "source", "sub": s["kind"], "title": s["title"], "uri": s["uri"], "origin": s["origin"], "captured": s["captured"], "text": s["text"][:6000],
                "has_file": bool(s["path"]), "used_by": [{"ref": f"item:{i}", "label": kb.item(i)["title"]} for i in kb.items_citing(ident) if kb.item(i)], "links": connections(ref),
                "history": kb.history(ref, 6), "note": OUTSIDE_NOTE if s["origin"] == "outside" else ""}
    if system == "world" and world is not None:
        got = world.recall(ident, include_private=include_private)["entities"]
        if not got:
            return None
        e = got[0]
        return {"ref": ref, "system": "world", "kind": e["kind"], "title": e["entity"], "facts": e["facts"], "decisions": e["decisions"], "world_links": e["links"], "links": connections(ref),
                "note": "Facts here live in the world model: change them with world_note."}
    if system == "vault":
        row = next((r for r in _vault_rows(services) if r["id"] == ident), None)
        return {"ref": ref, "system": "vault", "kind": "note", "title": row["title"], "tags": row.get("tags", []), "project": row.get("project", ""), "path": row.get("path", ""), "links": connections(ref)} if row else None
    if system == "mission" and services.get("jobs") is not None and ident.isdigit():
        job = services["jobs"].get(int(ident))
        if job is None:
            return None
        from minty_core.mind.control import contract

        return {"ref": ref, "system": "work", "kind": "mission", "title": job.goal, "contract": contract(job), "links": connections(ref)}
    if system == "decision" and world is not None:
        d = next((d for d in world.graph(include_private=include_private)["decisions"] if str(d["id"]) == ident), None)
        if d is None:
            return None
        return {"ref": ref, "system": "world", "kind": "decision", "title": d["title"], "kind_of_decision": d["kind"], "rationale": d["rationale"], "about": d["entities"], "links": connections(ref)}
    if system in ("flow", "playbook"):
        return {"ref": ref, "system": "work", "kind": system, "title": ident, "links": connections(ref)}
    return None
