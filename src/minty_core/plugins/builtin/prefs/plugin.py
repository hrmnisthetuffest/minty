"""Look at, set, accept, correct and forget what Minty has learned. Only the user's own words set a preference; a habit that was only noticed
is a suggestion until they accept it; and the kinds of preference are a short fixed list that cannot reach a permission."""
from pathlib import Path

from minty_core.plugins.host import Tool
from minty_core.policy import R1, R2, Mandate
from minty_core.prefs import PreferenceStore

# "from now on put my research in a sheet", "always ...": the words of someone setting a preference, not asking a question
SAY_SO = Mandate(r"\b(always|never|from now on|whenever|every time|prefer|remember (?:that|to)|i (?:like|want)|make it a habit|keep (?:putting|using))\b")


def register(ctx):
    store = ctx.state.get("store")  # one open database across reloads (every settings change reloads the plugins)
    if store is None:
        store = ctx.state["store"] = PreferenceStore(Path(ctx.config_dir) / "prefs.db")
    ctx.services["prefs"] = store
    ctx.services["prefers"] = lambda what: store.value(what, ("research", "global")) == "1"  # how research is finished (workflow.brief)

    async def listing(args):
        active, suggested = store.list(status="active"), store.suggestions()
        if not active and not suggested:
            return "Nothing learned yet."
        lines = [f"[{p.id}] {store.explain(p)}" for p in active]
        lines += [f"[{p.id}] Suggested: {store.explain(p)}" for p in suggested]
        return "\n".join(lines)

    async def set_(args):
        p = store.set_explicit(str(args.get("key") or ""), str(args.get("scope") or "global"), str(args.get("value") or "1"), note="you said so")
        return f"Saved: {store.explain(p)}"

    async def accept(args):
        return "Accepted: it now applies." if store.accept(int(args.get("id") or 0)) else "There is no such suggestion."

    async def reject(args):
        return "Okay, I will not suggest it again for a month." if store.reject(int(args.get("id") or 0)) else "There is no such item."

    async def correct(args):
        result = store.contradict(str(args.get("key") or ""), str(args.get("scope") or "global"))
        return {"lowered": "Noted: I trust that habit less now.", "removed": "That habit is removed and will not be offered for a month.",
                "unknown": "I have no such habit."}[result]

    async def forget(args):
        return "Forgot it." if store.forget(int(args.get("id") or 0)) else "There is no such item."

    def describe_accept(args):
        p = next((x for x in store.suggestions() if x.id == int(args.get("id") or 0)), None)
        return f"Keep this as a habit: {store.explain(p)}?" if p else "Keep a suggested habit?"

    s, i = {"type": "string"}, {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}
    return [
        Tool("list", "What Minty has learned about how the user works (and what it only suggests), with where each came from.",
             {"type": "object", "properties": {}}, listing),
        Tool("set", "Save a preference the user just stated (key such as research->sheet, scope such as research or global). Only their own words.",
             {"type": "object", "properties": {"key": s, "scope": s, "value": s}, "required": ["key"]}, set_, risk=R2, mandate=SAY_SO),
        Tool("accept", "The user agreed to a suggested habit (its id from prefs_list).", i, accept, risk=R2, describe=describe_accept),
        Tool("reject", "The user said no to a suggested habit: do not offer it again for a month.", i, reject, risk=R1),
        Tool("correct", "The user corrected what a habit led to: trust it less, and drop it on a second correction.",
             {"type": "object", "properties": {"key": s, "scope": s}, "required": ["key"]}, correct, risk=R1),
        Tool("forget", "Remove a learned preference for good.", i, forget, risk=R1),
    ]
