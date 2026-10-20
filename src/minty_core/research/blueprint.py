"""Blueprints: "I want to build X" -> parts, steps, alternatives, risks and unknowns, grounded in research.

The research engine finds and checks the claims; this module only structures them. Every part and step may cite
its sources, and a citation that is not a real source is removed. A second "what could go wrong" pass looks for
failure modes the first plan did not mention. With no model, the template's skeleton and the evidence are written
out and the note says so.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

Ask = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True)
class Template:
    name: str
    questions: tuple[str, ...]  # what to research besides the topic itself
    sections: str  # guidance for the structuring prompt
    watch_for: str  # guidance for the "what could go wrong" pass


TEMPLATES = {
    "electronics": Template(
        "electronics",
        ("components needed and typical part numbers", "wiring and circuit design", "power supply and current budget",
         "safety risks mains voltage batteries heat", "common failure modes and debugging"),
        "parts with quantities and ratings, wiring/assembly order, power budget, test procedure",
        "wrong voltage or polarity, overheating, short circuits, battery fire, missing flyback/ESD protection, "
        "firmware bugs, supply shortages"),
    "software": Template(
        "software",
        ("architecture and main components", "libraries and dependencies", "testing strategy", "deployment and hosting",
         "security and privacy risks"),
        "components, dependencies with versions, build order, tests, deployment",
        "scope creep, dependency breakage, security holes, data loss, performance cliffs, licence conflicts"),
    "fabrication": Template(
        "fabrication",
        ("materials and where to buy them", "tools and machines required", "tolerances and joinery or assembly method",
         "finishing", "workshop safety"),
        "materials with quantities and sizes, tools, cutting/assembly order, finishing",
        "wrong measurements, material waste, injury, tool misuse, warping, glue/finish incompatibility"),
    "general": Template(
        "general",
        ("what is needed to get started", "step by step approach", "common mistakes", "cost and time estimate"),
        "needs, steps, alternatives, risks",
        "missing prerequisites, cost overruns, time underestimates, safety"),
}
_KIND = {
    "electronics": re.compile(r"circuit|arduino|raspberry|esp32|sensor|led|battery|robot|solder|pcb|motor|wiring|"
                              r"microcontroller|drone|amplifier|power supply", re.I),
    "software": re.compile(r"app|website|software|program|script|api|bot|database|server|code|game|extension|"
                           r"plugin|tool", re.I),
    "fabrication": re.compile(r"wood|metal|3d print|furniture|shelf|table|frame|cnc|laser|weld|cut|build a (desk|box|"
                              r"bench)|cabinet|woodwork", re.I),
}
STRUCTURE_PROMPT = """You turn research claims into a project plan. Reply with JSON only:
{"parts": [{"item": "...", "qty": "...", "note": "...", "src": ["S1"]}],
 "steps": [{"step": "...", "src": ["S2"]}],
 "alternatives": [{"option": "...", "tradeoff": "..."}],
 "risks": [{"risk": "...", "mitigation": "..."}],
 "unknowns": ["..."]}
Use ONLY the numbered claims for facts and cite them in "src". Where the claims are silent, say so under "unknowns" \
instead of guessing a number. Plan emphasis: %s."""
RISK_PROMPT = """You are a careful reviewer. Given a project plan, list what could go wrong. Reply with JSON only: \
{"risks": [{"risk": "...", "mitigation": "..."}]} with at most 6 items, concrete and specific to this plan. \
Think about: %s. Do not repeat risks already listed."""


def detect_kind(goal: str) -> str:
    scored = {k: len(rx.findall(goal)) for k, rx in _KIND.items()}
    best = max(scored, key=scored.get)
    return best if scored[best] else "general"


@dataclass
class Blueprint:
    goal: str
    kind: str
    parts: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    used_model: bool = True


def _json_obj(text: str) -> dict | None:
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _clean_list(items, keys: tuple[str, ...], valid: set[str], cap: int) -> list[dict]:
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not str(item.get(keys[0]) or "").strip():
            continue
        row = {k: " ".join(str(item.get(k) or "").split())[:240] for k in keys}
        src = item.get("src")
        if "src" in item:
            row["src"] = [s for s in (src if isinstance(src, list) else []) if isinstance(s, str) and s in valid][:4]
        out.append(row)
        if len(out) >= cap:
            break
    return out


def parse_blueprint(reply: str, goal: str, kind: str, valid_sources: set[str]) -> Blueprint | None:
    data = _json_obj(reply)
    if not data:
        return None
    bp = Blueprint(goal, kind,
                   parts=_clean_list(data.get("parts"), ("item", "qty", "note"), valid_sources, 40),
                   steps=_clean_list(data.get("steps"), ("step",), valid_sources, 40),
                   alternatives=_clean_list(data.get("alternatives"), ("option", "tradeoff"), valid_sources, 10),
                   risks=_clean_list(data.get("risks"), ("risk", "mitigation"), valid_sources, 20),
                   unknowns=[" ".join(str(u).split())[:200] for u in data.get("unknowns", [])[:12]
                             if isinstance(u, str) and u.strip()])
    return bp if bp.parts or bp.steps else None


async def build(ask: Ask | None, goal: str, kind: str, claims: list[dict], sources: dict[str, dict]) -> Blueprint:
    template = TEMPLATES.get(kind, TEMPLATES["general"])
    valid = set(sources)
    listing = "\n".join(f"[{c['source']}] {c['text']}" for c in claims[:60])
    bp: Blueprint | None = None
    if ask is not None:
        try:
            reply = await ask(STRUCTURE_PROMPT % template.sections, f"Project: {goal}\n\nClaims:\n{listing}")
            bp = parse_blueprint(reply, goal, kind, valid)
        except Exception:  # noqa: BLE001
            bp = None
    if bp is None:
        return Blueprint(goal, kind, steps=[{"step": c["text"][:200], "src": [c["source"]]} for c in claims[:8]],
                         unknowns=["No model was available to structure this, so these are the checked findings "
                                   "as found; parts and order still need to be worked out."], used_model=False)
    if ask is not None:  # the "what could go wrong" pass
        try:
            plan = json.dumps({"parts": bp.parts, "steps": bp.steps})[:6000]
            reply = await ask(RISK_PROMPT % template.watch_for, f"Project: {goal}\n\nPlan: {plan}\n\n"
                              f"Risks already listed: {json.dumps([r['risk'] for r in bp.risks])}")
            more = _json_obj(reply) or {}
            seen = {r["risk"].lower() for r in bp.risks}
            for r in _clean_list(more.get("risks"), ("risk", "mitigation"), valid, 8):
                if r["risk"].lower() not in seen:
                    bp.risks.append(r)
        except Exception:  # noqa: BLE001
            pass
    return bp


def render(bp: Blueprint, sources: dict[str, dict], date_text: str) -> str:
    def cites(row: dict) -> str:
        return "".join(f" [{s}]" for s in row.get("src", []))

    lines = [f"# Blueprint: {bp.goal}", f"*{bp.kind} project · {date_text}"
             + ("" if bp.used_model else " · assembled without a model") + "*", ""]
    if bp.parts:
        lines += ["## Bill of materials"] + [
            f"- {p['item']}" + (f" × {p['qty']}" if p.get("qty") else "") + (f": {p['note']}" if p.get("note") else "")
            + cites(p) for p in bp.parts] + [""]
    lines += ["## Steps"] + [f"{i}. {s['step']}{cites(s)}" for i, s in enumerate(bp.steps, 1)] + [""]
    if bp.alternatives:
        lines += ["## Alternatives"] + [f"- {a['option']}: {a['tradeoff']}" for a in bp.alternatives] + [""]
    if bp.risks:
        lines += ["## What could go wrong"] + [f"- **{r['risk']}** — {r['mitigation']}" for r in bp.risks] + [""]
    if bp.unknowns:
        lines += ["## Unknowns (not covered by the sources)"] + [f"- {u}" for u in bp.unknowns] + [""]
    used = sorted({s for rows in (bp.parts, bp.steps) for row in rows for s in row.get("src", [])})
    if used:
        lines += ["## Sources"] + [f"{s}. {sources[s].get('title') or sources[s]['url']} — {sources[s]['url']}"
                                   for s in used if s in sources]
    return "\n".join(lines).strip() + "\n"
