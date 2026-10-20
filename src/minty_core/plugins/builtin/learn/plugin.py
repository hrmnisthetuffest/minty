import re
from datetime import date

from minty_core.plugins.host import Tool
from minty_core.policy import R1, R2
from minty_core.webmemory import Macro, MacroStep, MacroStore, Proposals


def register(ctx):
    macros = MacroStore(ctx.config_dir / "macros.json")
    proposals = Proposals(ctx.config_dir / "proposals.json")

    def today() -> str:
        return str(ctx.services.get("today", date.today)())

    async def remember(args):
        proposal = proposals.take(int(args["id"]) if args.get("id") is not None else None)
        if proposal is None:
            return "There's nothing waiting to be remembered. I offer after a job that finished and checked out."
        name = str(args.get("name") or "").strip() or " ".join(re.findall(r"[A-Za-z0-9]+", proposal["goal"])[:4])
        steps = [MacroStep(**s) for s in proposal["steps"]]
        macro = Macro(name, steps, status="draft", goal=proposal["goal"], changelog="drafted from a finished job")
        try:
            macros.save(macro)
        except ValueError as e:
            return f"Error: {e}"
        return (f"Saved a draft routine called {macro.name!r}: {macro.summary()}. It is not active yet: run it once "
                f"with browser_macro_run to test it, then say approve.")

    async def listing(args):
        names = macros.names()
        if not names:
            return "No routines yet."
        out = []
        for n in names:
            m = macros.get(n)
            if m is not None:
                out.append(f"{m.name} ({m.status}, v{m.version}, {m.runs} runs, {m.failures} failed)")
        return "; ".join(out) + "."

    async def approve(args):
        name = str(args.get("name") or "")
        macro = macros.get(name)
        if macro is None:
            return f"Error: no routine called {name!r}."
        why_not = macros.approve(name, today())
        return f"Error: not approved, {why_not}." if why_not else (
            f"Approved. {macro.name!r} is active (version {macro.version + 1}).")

    async def forget(args):
        return "Forgotten." if macros.delete(str(args.get("name") or "")) else "No routine with that name."

    async def review(args):
        weak = []
        for n in macros.names():
            m = macros.get(n)
            if m is None:
                continue
            if m.status == "quarantined":
                weak.append(f"{m.name}: quarantined ({m.changelog})")
            elif m.runs >= 3 and m.failures / m.runs > 0.3:
                weak.append(f"{m.name}: failed {m.failures} of {m.runs} runs")
        return ("These routines look weak: " + "; ".join(weak) + ". I won't change them on my own.") if weak else \
            "All routines are healthy."

    def approve_prompt(args) -> str:
        macro = macros.get(str(args.get("name") or ""))
        what = macro.summary() if macro else "an unknown routine"
        return f"Should I make the routine {str(args.get('name') or '')[:40]!r} active? It has {what}."

    name_arg = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    return [
        Tool("remember", "After a browser job you offered to remember, and the user said yes: save it as a draft "
                         "routine. name is optional.",
             {"type": "object", "properties": {"name": {"type": "string"}, "id": {"type": "integer"}}}, remember,
             risk=R1),
        Tool("list", "List saved routines with their status.", {"type": "object", "properties": {}}, listing),
        Tool("approve", "Make a tested draft routine active. The user is shown what it does and must say yes.",
             name_arg, approve, risk=R2, describe=approve_prompt),
        Tool("forget", "Delete a saved routine.", name_arg, forget, risk=R1),
        Tool("review", "Which routines are failing or quarantined (a report only).",
             {"type": "object", "properties": {}}, review),
    ]
