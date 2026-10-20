import contextlib
import json
import logging
import re
import time
from pathlib import Path

from minty_core.llm.types import Message, TextDelta
from minty_core.plugins.host import Tool
from minty_core.policy import R1, R3
from minty_core.research import blueprint as bplib
from minty_core.research.engine import DEPTHS, Research, ResearchError
from minty_core.research.library import Library
from minty_core.research.text import html_to_text
from minty_core.safefetch import FetchFailed, safe_fetch

log = logging.getLogger(__name__)


def register(ctx):
    settings = ctx.settings or {}
    workspace = Path(str(settings.get("folder") or "~/Minty-Workspace")).expanduser()
    # one open library per place, kept across reloads (every settings change reloads the plugins)
    place = (str(workspace / "library"), str(ctx.config_dir / "library.db"))
    held = ctx.state.get("library")
    if held is None or held[0] != place:
        if held is not None:
            held[1].close()
        held = (place, Library(workspace / "library", ctx.config_dir / "library.db"))
        ctx.state["library"] = held
    library = held[1]
    state_dir = ctx.config_dir / "research"
    deny = {d.strip().lower() for d in str(settings.get("deny_domains") or "").split(",") if d.strip()}
    allow = {d.strip().lower() for d in str(settings.get("allow_domains") or "").split(",") if d.strip()}
    model = str(settings.get("model") or "").strip() or None

    async def ask(system: str, user: str) -> str:
        router = ctx.services.get("router")
        if router is None:
            raise RuntimeError("no model")
        parts: list[str] = []
        stream = router.stream([Message("system", system), Message("user", user)], [], sensitive=False, prefer=model)
        async with contextlib.aclosing(stream) as events:
            async for event in events:
                if isinstance(event, TextDelta):
                    parts.append(event.text)
        return "".join(parts)

    async def get(url: str, params: dict | None = None) -> str:
        fetched = await safe_fetch("GET", url, params=params, max_bytes=1_500_000)
        return fetched.data.decode(fetched.charset or "utf-8", "replace")

    async def fetch(url: str) -> tuple[str, str, str]:
        try:
            got = await safe_fetch("GET", url, max_bytes=1_500_000)
        except FetchFailed as e:
            return "", "", f"could not be read ({e})"
        kind = (got.content_type or "").lower()
        if "pdf" in kind:
            return "", "", "a PDF, not read"
        text = got.data.decode(got.charset or "utf-8", "replace")
        if "html" in kind or text.lstrip().startswith("<"):
            title, body = html_to_text(text)
            return title, body, "read"
        return "", text[:40_000], "read"

    def engine(progress=lambda t: None) -> Research:
        return Research(ask=ask, get=ctx.services.get("research_get") or get,
                        fetch=ctx.services.get("research_fetch") or fetch, library=library, state_dir=state_dir,
                        progress=progress, deny=deny, allow=allow, providers=ctx.services.get("research_providers"),
                        tracer=ctx.services.get("tracer"))

    def remember(note) -> None:
        gists = ctx.services.get("gists")
        if gists is None or note.ref is None:
            return
        bullets = re.findall(r"^- (.+?)(?:\s*\[S\d+\])+.*$", note.markdown, re.M)[:4]
        for b in bullets:
            gists.add("research", f"{note.topic}: {b}"[:160], title="research", category=note.ref.category,
                      summary=note.summary, source_ref=f"library/{note.ref.category}/{note.ref.slug}",
                      importance_=7)

    async def finish(topic: str, depth: str, run_id: str, progress) -> str:
        try:
            note = await engine(progress).run(topic, depth, run_id)
        except ResearchError as e:
            raise RuntimeError(str(e)) from e
        remember(note)
        return note.summary

    async def draft_blueprint(goal: str, kind: str, depth: str, run_id: str, progress) -> str:
        template = bplib.TEMPLATES.get(kind, bplib.TEMPLATES["general"])
        eng = engine(progress)
        try:
            note = await eng.run(goal, depth, run_id, questions=list(template.questions))
        except ResearchError as e:
            raise RuntimeError(str(e)) from e
        state = eng.load(run_id) or {}
        progress("drafting the blueprint")
        sources = state.get("sources", {})
        claims = sorted(state.get("claims", []), key=lambda c: c.get("source", ""))
        plan = await bplib.build(ask, goal, kind, claims, sources)
        text = bplib.render(plan, sources, time.strftime("%d %B %Y"))
        library.move(note.ref.slug, "Projects")
        library.attach(note.ref.slug, "blueprint.md", text)
        library.attach(note.ref.slug, "blueprint.json", json.dumps(
            {"goal": goal, "kind": kind, "parts": plan.parts, "steps": plan.steps, "alternatives": plan.alternatives,
             "risks": plan.risks, "unknowns": plan.unknowns}, ensure_ascii=False, indent=1))
        remember(note)
        worst = plan.risks[0]["risk"] if plan.risks else "none found"
        return (f"The {kind} blueprint for {goal} is ready: {len(plan.parts)} parts, {len(plan.steps)} steps, "
                f"{len(plan.risks)} risks flagged; the first to watch is {worst}. It's in the library under Projects.")

    async def blueprint(args):
        goal = " ".join(str(args.get("goal") or "").split())[:200]
        depth = str(args.get("depth") or "normal").lower()
        kind = str(args.get("kind") or "").lower() or bplib.detect_kind(goal)
        if not goal:
            return "Error: say what to build."
        if depth not in DEPTHS or kind not in bplib.TEMPLATES:
            return f"Error: depth is light, normal or deep; kind is {', '.join(bplib.TEMPLATES)}."
        if ctx.services.get("is_private", lambda: False)():
            return "Error: a blueprint uses research on the internet, so it does not run in private mode."
        run_id = Research.new_id()
        runner = ctx.services.get("jobs")
        if runner is None:
            return await draft_blueprint(goal, kind, depth, run_id, lambda t: None)

        async def work(job_ctx):
            return await draft_blueprint(goal, kind, depth, run_id, job_ctx.progress)

        job = runner.submit("research", f"blueprint for {goal}", work, budget_s=DEPTHS[depth].seconds + 180)
        return f"On it: a {kind} blueprint for {goal}, job {job.id}, about {DEPTHS[depth].minutes}."

    def blueprint_prompt(args) -> str:
        depth = str(args.get("depth") or "normal").lower()
        minutes = DEPTHS.get(depth, DEPTHS["normal"]).minutes
        return (f"Should I research and draft a blueprint for “{str(args.get('goal') or '')[:80]}”? It runs in the "
                f"background and takes about {minutes}.")

    async def start(args):
        topic = " ".join(str(args.get("topic") or "").split())[:200]
        depth = str(args.get("depth") or "normal").lower()
        if not topic:
            return "Error: say what to research."
        if depth not in DEPTHS:
            return f"Error: depth is light, normal or deep."
        if ctx.services.get("is_private", lambda: False)():
            return "Error: research uses the internet, so it does not run in private mode. Turn private mode off first."
        run_id = Research.new_id()
        runner = ctx.services.get("jobs")
        if runner is None:
            return await finish(topic, depth, run_id, lambda t: None)

        async def work(job_ctx):
            return await finish(topic, depth, run_id, job_ctx.progress)

        job = runner.submit("research", f"research {topic}", work, budget_s=DEPTHS[depth].seconds + 60)
        return (f"On it: {depth} research on {topic}, job {job.id}, about {DEPTHS[depth].minutes}. "
                "I'll tell you when it's done.")

    def start_prompt(args) -> str:
        depth = str(args.get("depth") or "normal").lower()
        minutes = DEPTHS.get(depth, DEPTHS["normal"]).minutes
        return (f"Should I start a {depth} research on “{str(args.get('topic') or '')[:80]}”? It runs in the "
                f"background and takes about {minutes}.")

    async def resume(args):
        pending = engine().unfinished()
        if not pending:
            return "No unfinished research."
        run_id, st = pending[-1]
        runner = ctx.services.get("jobs")
        if runner is None:
            return await finish(st["topic"], st["depth"], run_id, lambda t: None)

        async def work(job_ctx):
            return await finish(st["topic"], st["depth"], run_id, job_ctx.progress)

        job = runner.submit("research", f"research {st['topic']}", work,
                            budget_s=DEPTHS[st["depth"]].seconds + 60)
        return f"Carrying on with the {st['depth']} research on {st['topic']}, job {job.id}."

    async def ask_library(args):
        question = str(args.get("question") or "")
        hits, metas = library.ask(question)
        if not hits:
            return "I have nothing on that in the library. Want me to research it? Light, normal or deep."
        lines, notes = [], []
        for h in hits:
            lines.append(f"- {h['claim']} [{h['source']}] ({h['domain']}, claim {h['id']})")
            if h["note"] not in notes:
                notes.append(h["note"])
        kinds = {h["kind"] for h in hits}
        stale = [library.freshness(metas[n], kinds) for n in notes if metas.get(n)]
        topics = ", ".join(f"“{metas[n].get('topic', n)}”" for n in notes if metas.get(n))
        out = f"From my research notes on {topics}:\n" + "\n".join(lines)
        return out + ("\n" + stale[0] if stale and stale[0] else "")

    async def listing(args):
        items = library.list(str(args.get("category") or "") or None)
        if not items:
            return "The library is empty."
        return "; ".join(f"{m['slug']} ({m['category']}, {m['depth']}, {m['sources']} sources, {m['confidence']})"
                         for m in items[:12]) + "."

    async def open_note(args):
        slug = str(args.get("slug") or "")
        note = library.note(slug)
        if not note:
            return f"Error: no research called {slug!r}."
        publish = ctx.services.get("publish")
        if publish is not None:
            publish("display", title=library.meta(slug).get("topic", slug), content=note)
        return (library.meta(slug) or {}).get("summary", "Shown on the screen.")

    async def move(args):
        return library.move(str(args.get("slug") or ""), str(args.get("category") or ""))

    async def dispute(args):
        try:
            domain = library.dispute(int(args.get("claim")))
        except (TypeError, ValueError):
            return "Error: give the claim number."
        return (f"Marked that claim as wrong and I'll trust {domain} a little less." if domain
                else "I don't have a claim with that number.")

    q = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}
    return [
        Tool("start", "Research a topic in the background. depth: light (5-8 sources), normal (15-20) or deep (about "
                      "80). Only after the user asked for research or agreed to your offer.",
             {"type": "object", "properties": {"topic": {"type": "string"},
                                               "depth": {"type": "string", "enum": ["light", "normal", "deep"]}},
              "required": ["topic"]}, start, risk=R3, describe=start_prompt),
        Tool("blueprint", "Research and draft a project blueprint (parts, steps, alternatives, risks, unknowns) for "
                          "something the user wants to build. kind: electronics, software, fabrication or general. "
                          "Only when asked.",
             {"type": "object", "properties": {"goal": {"type": "string"}, "kind": {"type": "string"},
                                               "depth": {"type": "string", "enum": ["light", "normal", "deep"]}},
              "required": ["goal"]}, blueprint, risk=R3, describe=blueprint_prompt),
        Tool("resume", "Carry on with research that was interrupted.", {"type": "object", "properties": {}}, resume,
             risk=R1),
        Tool("ask", "Answer a question from the saved research notes, with citations. Use before searching.", q,
             ask_library),
        Tool("list", "List saved research notes, optionally for one category.",
             {"type": "object", "properties": {"category": {"type": "string"}}}, listing),
        Tool("open", "Show a saved research note on the screen.",
             {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]}, open_note),
        Tool("move", "File a research note under another category (e.g. Health).",
             {"type": "object", "properties": {"slug": {"type": "string"}, "category": {"type": "string"}},
              "required": ["slug", "category"]}, move, risk=R1),
        Tool("dispute", "The user says a claim from the notes is wrong: mark it and trust its site less.",
             {"type": "object", "properties": {"claim": {"type": "integer"}}, "required": ["claim"]}, dispute,
             risk=R1),
    ]
