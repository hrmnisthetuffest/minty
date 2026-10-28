"""The sheets and docs Minty may write to. Adding one is something only the user's own words can do (a tool whose argument must appear in
what the user said), so a page or an e-mail cannot name a sheet to be written to."""
from pathlib import Path

from minty_core.artifacts import KINDS, ArtifactStore
from minty_core.plugins.host import Tool
from minty_core.policy import R1, R2, Mandate

# "use this sheet <link>", "track the leads in <link>": the link must be in the user's own words, or the question is asked
REGISTER = Mandate(r"\b(use|using|track|put|add|log|update|save|write|keep|sheet|spreadsheet|doc|document|in|into|to)\b", targets=("ref",))


def register(ctx):
    store = ctx.state.get("store")  # one open database across reloads (every settings change reloads the plugins)
    if store is None:
        store = ctx.state["store"] = ArtifactStore(Path(ctx.config_dir) / "artifacts.db")
    ctx.services["artifacts"] = store

    def ref_of(args) -> str:
        return ArtifactStore.parse_ref(str(args.get("ref") or ""))[1]

    async def listing(args):
        found = store.find(kind=str(args.get("kind") or "") or None, key=str(args.get("key") or "") or None)
        if not found:
            return "No sheets or docs yet."
        return "\n".join(f"{a.kind}: {a.title or 'Untitled'} ({a.url or a.ext_id})" + (f" for {a.key}" if a.key else "")
                         + (" — you named it" if a.source == "user" else " — Minty made it") for a in found)

    async def add(args):
        ref = str(args.get("ref") or "")
        kind, ext_id = ArtifactStore.parse_ref(ref)
        if not ext_id:
            return "That is not a Google Docs or Sheets link or id, so I did not add it."
        kind = kind or (args["kind"] if args.get("kind") in KINDS else "sheet")  # a plain id does not say which
        link = ref.split("#")[0] if ref.startswith("https://") else ""
        a = store.add(kind, ext_id, str(args.get("title") or "Untitled"), url=link, key=str(args.get("key") or ""), source="user")
        return f"Added the {a.kind} {a.title!r}: Minty may now write to it."

    async def tag(args):
        return (f"Tagged it {str(args.get('key'))[:60]!r}." if store.tag(ref_of(args), str(args.get("key") or ""))
                else "That is not one I know.")

    async def forget(args):
        return "Forgot it: Minty will no longer write to it." if store.forget(ref_of(args)) else "That is not one I know."

    s = {"type": "string"}
    return [
        Tool("list", "The Google sheets and docs Minty made or you named (kind, title, link, what it is for). Look here before making a new one.",
             {"type": "object", "properties": {"kind": {"type": "string", "enum": ["sheet", "doc"]}, "key": s}}, listing),
        Tool("register", "Add a sheet or doc the user gave you (link or id) so you may write to it. Only with their link.",
             {"type": "object", "properties": {"ref": s, "title": s, "key": s, "kind": {"type": "string", "enum": list(KINDS)}}, "required": ["ref"]}, add, risk=R2, mandate=REGISTER),
        Tool("forget", "Stop writing to a sheet or doc (link or id).", {"type": "object", "properties": {"ref": s}, "required": ["ref"]}, forget, risk=R1),
        Tool("tag", "Say what a known sheet or doc is for (link or id, key such as a project name), so the next job finds it.",
             {"type": "object", "properties": {"ref": s, "key": s}, "required": ["ref", "key"]}, tag, risk=R1),
    ]
