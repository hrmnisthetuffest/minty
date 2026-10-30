"""Minty's hands on the Second Brain.

Five tools: capture (anything in), recall (with who wrote it and whether it is checked), link (connect), edit (words, status, archive, the inbox) and check (what is missing,
what disagrees, what waits). Rules that are enforced here, not left to the model: a turn that read outside content (mail, a page, a file) cannot file anything as "yours", cannot
verify, cannot confirm a link and can only put things in the inbox for approval; a private turn files private; and nothing here deletes (archiving keeps everything, and approving
or deleting a capture is the owner's, from the panel).
"""
import json

from minty_core.knowledge.reason import OUTSIDE_NOTE
from minty_core.knowledge.store import KINDS, KnowledgeError, parse_ref
from minty_core.plugins.host import Tool
from minty_core.policy import R0, R1


def register(ctx):
    def need(name):
        got = ctx.services.get(name)
        if got is None:
            raise RuntimeError("the second brain is not running here")
        return got

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
            except (KnowledgeError, ValueError, LookupError, RuntimeError, KeyError, OSError) as e:
                return f"Not done: {e}"
        return run

    def piece(a: dict) -> dict:
        return {"id": a["item"], "kind": a["kind"], "title": a["title"], "about": a["entities"], "duplicate_of": a["duplicate_of"] or None,
                "similar": [{"ref": s["ref"], "why": s["why"]} for s in a["similar"]]}

    def capture_view(c: dict) -> dict:
        res = c["result"]
        view = {"capture": c["id"], "status": c["status"], "attempts": c["attempts"], "error": c["error"], "pieces": [piece(a) for a in res.get("atoms", [])],
                "proposed_names": res.get("entities", {}).get("proposed", []), "flags": res.get("flags", []), "notes": res.get("notes", [])}
        view["next"] = {"filed": "Filed. Connect pieces with know_link, or see what is missing with know_check.",
                        "review": "Waiting for the owner: they approve it in the panel (Second Brain, Inbox) because it came from outside or reads like instructions.",
                        "failed": "It failed and is kept in the inbox: fix the cause and reprocess with know_edit (do=reprocess).",
                        "pending": "In the inbox, not processed yet."}.get(c["status"], "")
        return view

    async def capture(args):
        kb, cap = need("knowledge"), need("knowledge_capture")
        o, p = outside(), private()
        dump = str(args.get("text") or "") or (str(args.get("body") or "") if cap.looks_like_dump(str(args.get("body") or "")) else "")
        if args.get("title") and not dump:  # a "titled item" whose body is really several labelled thoughts is a brain dump: it is split like one
            kind = str(args.get("kind") or "note")
            added = cap.add(kind, str(args["title"]), str(args.get("body") or ""), authored=str(args.get("authored") or "minty"), summary=str(args.get("summary") or ""),
                            project=str(args.get("project") or ""), tags=args.get("tags") or (), private=p, sources=[s for s in (args.get("sources") or []) if isinstance(s, str)],
                            actor="minty", outside=o)
            it = added["item"]
            made, problems = [], []
            for link in ([] if added["duplicate"] else args.get("links") or []):
                try:
                    made.append(kb.link(f"item:{it['id']}", str(link.get("rel") or "relates to"), str(link.get("to") or ""), why=str(link.get("why") or ""), by="minty", outside=o)["dst"])
                except KnowledgeError as e:
                    problems.append(str(e))
            return out({"id": it["id"], "kind": it["kind"], "authored": it["authored"], "status": it["status"], "duplicate": added["duplicate"], "about": added["about"],
                        "similar": [{"ref": x["ref"], "why": x["why"]} for x in added["similar"]], "linked_to": made, "link_problems": problems,
                        "note": "this was already in the second brain: the new source was added as evidence, not a second copy" if added["duplicate"] else
                        "from outside, unchecked: it needs a second source" if o else "written by Minty, not checked until a source is attached or the owner confirms it"})
        got = await cap.capture(text=dump, url=str(args.get("url") or ""), channel="agent", kind_hint=str(args.get("kind") or ""),
                                origin="outside" if o else "you", private=p)
        return out(capture_view(got))

    async def recall(args):
        kb, brain = need("knowledge"), need("knowledge_reason")
        p = private()
        if args.get("ref"):
            system, ident = parse_ref(str(args["ref"]))
            if system == "item":
                it = kb.item(ident)
                if it is None or (it["private"] and not p):
                    return "Not done: there is no such item."
                links = [{"id": x["id"], "rel": x["rel"], "to": brain._label(x["dst"] if x["src"] == f"item:{ident}" else x["src"]), "ref": x["dst"] if x["src"] == f"item:{ident}" else x["src"],
                          "status": x["status"], "why": x["why"]} for x in kb.links(f"item:{ident}", statuses=("confirmed", "suggested"))]
                return out({"id": it["id"], "kind": it["kind"], "title": it["title"], "body": it["body"][:2000], "summary": it["summary"], "authored": it["authored"], "status": it["status"],
                            "tags": it["tags"], "project": it["project"], "created": it["created"], "updated": it["updated"], "reviewed": it["reviewed"], "archived": it["archived"],
                            "evidence": kb.evidence(ident), "links": links, "history": kb.history(f"item:{ident}", 6), "related": brain.related_to_item(ident)[:3],
                            "note": OUTSIDE_NOTE if it["authored"] == "outside" else ""})
            if system == "source":
                s = kb.source(ident)
                if s is None or (s["private"] and not p):
                    return "Not done: there is no such source."
                return out({"id": s["id"], "kind": s["kind"], "title": s["title"], "uri": s["uri"], "origin": s["origin"], "captured": s["captured"], "text": s["text"][:2000],
                            "used_by": [kb.item(i)["title"] for i in kb.items_citing(ident) if kb.item(i)], "note": OUTSIDE_NOTE if s["origin"] == "outside" else ""})
            return out({"ref": args["ref"], "connected": brain.neighbours(str(args["ref"]))})
        data = brain.recall(str(args.get("query") or ""), include_private=p)
        for e in data["world"]:
            e["facts"] = e["facts"][:5]
        return out(data)

    async def link(args):
        kb, o = need("knowledge"), outside()
        action = str(args.get("action") or "link")
        if action in ("confirm", "reject"):
            return out(kb.set_link(int(args["id"]), "confirmed" if action == "confirm" else "rejected", actor="minty", outside=o))
        if action == "unlink":
            return out({"ok": kb.unlink(str(args["src"]), str(args.get("rel") or "relates to"), str(args["dst"]), actor="minty")})
        got = kb.link(str(args["src"]), str(args.get("rel") or "relates to"), str(args["dst"]), why=str(args.get("why") or ""), by="minty", outside=o)
        if o and got["status"] == "suggested":
            got["note"] = "this turn read outside content, so it is only a suggestion until the owner confirms it"
        return out(got)

    def item_view(it: dict) -> dict:
        return {k: it[k] for k in ("id", "kind", "title", "body", "summary", "authored", "status", "archived", "tags", "project")}

    async def edit(args):
        kb, cap, o = need("knowledge"), need("knowledge_capture"), outside()
        ident = str(args.get("id") or "")
        if ident.startswith("c-"):
            do = str(args.get("do") or "reprocess")
            if do == "approve":
                return "Not done: approving a capture is the owner's. They do it in the panel (Second Brain, Inbox)."
            if do == "archive":
                got = kb.update_capture(ident, status="archived", actor="minty")
            elif do == "edit":
                got = kb.update_capture(ident, raw=str(args.get("text") or ""), status="pending", actor="minty")
                got = await cap.process(ident) if args.get("process", True) else got
            else:
                got = await cap.process(ident)
            return out(capture_view(got))
        fields = {k: args[k] for k in ("title", "body", "summary", "kind", "project", "tags") if args.get(k) is not None}
        if fields:
            kb.update_item(ident, actor="minty", outside=o, **fields)
        if args.get("status"):
            kb.set_status(ident, str(args["status"]), actor="minty", outside=o, note=str(args.get("why") or ""))
        if args.get("archive") is not None:
            kb.archive(ident, bool(args["archive"]), actor="minty")
        if args.get("reviewed"):
            kb.mark_reviewed(ident, actor="minty")
        it = kb.item(ident)
        if it is None:
            return "Not done: there is no such item."
        return out(item_view(it))

    async def check(args):
        kb, brain, p = need("knowledge"), need("knowledge_reason"), private()
        what = str(args.get("what") or "overview")
        if what == "gaps":
            return out({"gaps": brain.gaps(p)[:15]})
        if what == "conflicts":
            return out({"conflicts": brain.conflicts(p)[:12], "note": "verified = linked as contradicting or two world facts; anything else is a guess from the wording"})
        if what == "inbox":
            return out({"captures": [{"id": c["id"], "status": c["status"], "channel": c["channel"], "text": c["raw"][:120], "error": c["error"]} for c in kb.captures(include_private=p)[:15]]})
        if what == "related":
            return out({"related": brain.related_to_item(str(args.get("id") or ""))})
        if what == "health":
            return out({"health": brain.health(p), **kb.stats(p)})
        return out({"health": brain.health(p), "gaps": brain.gaps(p)[:6], "conflicts": brain.conflicts(p)[:4]})

    s, b = {"type": "string"}, {"type": "boolean"}
    return [
        Tool("capture", "Put anything in the second brain. For what the owner said use text (a thought or brain dump: split, linked, de-duplicated); url (a page); title+kind+body only for something you worked out (links [{rel,to}], sources [ids], authored you/minty). Outside content waits for the owner.",
             {"type": "object", "properties": {"text": s, "url": s, "title": s, "kind": {"type": "string", "enum": list(KINDS)}, "body": s, "summary": s, "tags": {"type": "array", "items": s}, "project": s,
                                               "sources": {"type": "array", "items": s}, "authored": {"type": "string", "enum": ["you", "minty"]},
                                               "links": {"type": "array", "items": {"type": "object", "properties": {"rel": s, "to": s, "why": s}}}}}, guarded(capture), risk=R1),
        Tool("recall", "What the second brain knows: query finds items (who wrote each, checked or not, sources, links) plus world facts; ref (item:ID or source:ID) opens one with evidence, links, history.",
             {"type": "object", "properties": {"query": s, "ref": s}}, guarded(recall), sensitive=True, risk=R0),
        Tool("link", "Connect two things by reference (item:ID, source:ID, world:NAME, vault:ID, mission:ID, flow:ID). rel: supports, contradicts, answers, part of, derived from, about, relates to... action link, confirm or reject (by id), unlink.",
             {"type": "object", "properties": {"src": s, "rel": s, "dst": s, "why": s, "id": {"type": "integer"}, "action": {"type": "string", "enum": ["link", "confirm", "reject", "unlink"]}}}, guarded(link), risk=R1),
        Tool("edit", "Change an item (title, body, summary, kind, tags, project), set status (verified needs a source), archive true/false, reviewed; or work the inbox: id c-... with do reprocess, archive, edit.",
             {"type": "object", "properties": {"id": s, "title": s, "body": s, "summary": s, "kind": s, "project": s, "tags": {"type": "array", "items": s}, "status": {"type": "string", "enum": ["unverified", "verified", "disputed", "outdated"]},
                                               "why": s, "archive": b, "reviewed": b, "do": {"type": "string", "enum": ["reprocess", "archive", "edit", "approve"]}, "text": s}, "required": ["id"]}, guarded(edit), risk=R1),
        Tool("check", "Where the second brain stands: gaps (open questions, unsourced claims, isolated items), conflicts (verified or guessed), inbox, related (for id), health. Default: overview.",
             {"type": "object", "properties": {"what": {"type": "string", "enum": ["overview", "gaps", "conflicts", "inbox", "related", "health"]}, "id": s}}, guarded(check), risk=R0),
    ]
