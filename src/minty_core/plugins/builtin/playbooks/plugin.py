"""Playbooks: only the user's own words create one, an improvement is a proposal, accepting it is asked, and going back is one step."""
from pathlib import Path

from minty_core.playbooks import PlaybookStore
from minty_core.plugins.host import Tool
from minty_core.policy import R1, R2, Mandate

# "make this a playbook", "save that as a template": the words of someone creating one
CREATE = Mandate(r"\b(playbook|play book|template|standard way|procedure|workflow|routine)\b")


def register(ctx):
    store = ctx.state.get("store")  # one open database across reloads (every settings change reloads the plugins)
    if store is None:
        store = ctx.state["store"] = PlaybookStore(Path(ctx.config_dir) / "playbooks.db")
    ctx.services["playbooks"] = store

    async def listing(args):
        names = store.names()
        if not names:
            return "No playbooks yet."
        return "\n".join(f"{name}: {store.compare(name)}" for name in names)

    async def create(args):
        v = store.create(str(args.get("name") or ""), str(args.get("triggers") or ""), str(args.get("steps") or ""), str(args.get("context") or ""),
                         str(args.get("output") or ""), str(args.get("checks") or ""), str(args.get("recovery") or ""))
        return f"Saved the playbook {v.name}: it will be offered to missions whose goal looks like it."

    async def propose(args):
        fields = {k: args.get(k) for k in ("steps", "context", "output", "checks", "recovery") if args.get(k) is not None}
        v = store.propose(str(args.get("name") or ""), note=str(args.get("note") or ""), **fields)
        return f"Proposed version {v.version} of {v.name}. Nothing changes until the user accepts it. {store.compare(v.name)}"

    async def accept(args):
        return "Accepted: it is the version used now." if store.accept(str(args.get("name") or ""), int(args.get("version") or 0)) else "There is no such proposal."

    async def rollback(args):
        return "Went back to the version before." if store.rollback(str(args.get("name") or "")) else "There is no earlier version."

    async def remove(args):
        return "Removed it." if store.remove(str(args.get("name") or "")) else "There is no such playbook."

    async def stats(args):
        return store.compare(str(args.get("name") or ""))

    def describe_accept(args):
        name, version = str(args.get("name") or ""), int(args.get("version") or 0)
        return f"Use version {version} of the playbook {name} from now on? {store.compare(name)}"

    s, v = {"type": "string"}, {"type": "integer"}
    named = {"type": "object", "properties": {"name": s}, "required": ["name"]}
    fields = {"steps": s, "context": s, "output": s, "checks": s, "recovery": s}
    return [
        Tool("list", "The playbooks (reusable ways of doing a kind of work) and how each version has done.", {"type": "object", "properties": {}}, listing),
        Tool("create", "Save a playbook the user described: name, triggers (words that say what goals it is for), steps, and optionally context, output, "
                       "checks, recovery. Only from the user's own words.",
             {"type": "object", "properties": {"name": s, "triggers": s, **fields}, "required": ["name", "triggers", "steps"]}, create, risk=R2, mandate=CREATE),
        Tool("propose", "Propose an improved version of a playbook. It changes nothing until the user accepts it.",
             {"type": "object", "properties": {"name": s, "note": s, **fields}, "required": ["name"]}, propose, risk=R1),
        Tool("accept", "The user accepted a proposed version of a playbook.", {"type": "object", "properties": {"name": s, "version": v}, "required": ["name", "version"]},
             accept, risk=R2, describe=describe_accept),
        Tool("rollback", "Go back to the version before the active one.", named, rollback, risk=R1),
        Tool("remove", "Remove a playbook.", named, remove, risk=R1),
        Tool("stats", "How a playbook's versions have done: runs, how many finished, how long.", named, stats),
    ]
