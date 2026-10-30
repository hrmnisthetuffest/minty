"""The panel's HTTP routes for the second brain: the map, one thing opened, search, what needs attention, and the inbox with everything the owner can do to it.

What is done here is done by the owner (`you`): a link made here is confirmed, an item added here is yours, "verified" can be set without a source, and an inbox entry waiting for approval
can be approved. Nothing here deletes knowledge: an item is archived, a link is rejected, and only an inbox entry can be removed for good. The token and the CSRF header are checked by the
router these routes are added to.
"""
from __future__ import annotations

import base64
import binascii

from fastapi import Body, HTTPException

from minty_core.knowledge import graph
from minty_core.knowledge.store import KnowledgeError

MAX_UPLOAD = 8 * 1024 * 1024


def add_routes(api, ctx) -> None:
    def need(name: str):
        got = ctx.services.get(name)
        if got is None:
            raise HTTPException(status_code=503, detail="the second brain is not running")
        return got

    def safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KnowledgeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'\""))

    async def asafe(coro):
        try:
            return await coro
        except KnowledgeError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ---- the map, one thing, search, attention -----------------------------------------------------------------------------
    @api.get("/knowledge/graph")
    async def knowledge_graph(private: bool = False, limit: int = 900, focus: str = ""):
        need("knowledge")
        return graph.build(ctx.services, include_private=private, max_nodes=max(20, min(limit, 2000)), focus=focus)

    @api.get("/knowledge/node")
    async def knowledge_node(ref: str, private: bool = False):
        need("knowledge")
        got = graph.node(ctx.services, ref, include_private=private)
        if got is None:
            raise HTTPException(status_code=404, detail="no such thing")
        return got

    @api.get("/knowledge/search")
    async def knowledge_search(q: str = "", private: bool = False):
        need("knowledge")
        return {"query": q, "hits": graph.find(ctx.services, q, include_private=private)}

    @api.get("/knowledge/attention")
    async def knowledge_attention(private: bool = False):
        kb, brain = need("knowledge"), need("knowledge_reason")
        waiting = [c for c in kb.captures(include_private=private) if c["status"] in ("pending", "review", "failed")]
        return {"health": brain.health(private), "gaps": brain.gaps(private)[:25], "conflicts": brain.conflicts(private)[:15],
                "captures": [{"id": c["id"], "status": c["status"], "text": c["raw"][:120], "error": c["error"]} for c in waiting[:15]], "stats": kb.stats(private)}

    # ---- the inbox -----------------------------------------------------------------------------------------------------------
    @api.get("/knowledge/captures")
    async def captures_list(status: str = "", private: bool = False):
        return need("knowledge").captures(status, include_private=private)

    @api.post("/knowledge/captures")
    async def captures_add(payload: dict = Body(...)):
        cap = need("knowledge_capture")
        return await asafe(cap.capture(text=str(payload.get("text") or ""), url=str(payload.get("url") or ""), channel="panel", kind_hint=str(payload.get("kind") or ""),
                                       origin="you", private=bool(payload.get("private")), process=payload.get("process", True) is not False))

    @api.post("/knowledge/captures/upload")
    async def captures_upload(payload: dict = Body(...)):
        cap = need("knowledge_capture")
        try:
            data = base64.b64decode(str(payload.get("data") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="the file was not sent correctly")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail=f"files over {MAX_UPLOAD // (1024 * 1024)} MB are not taken here")
        if not data:
            raise HTTPException(status_code=400, detail="the file is empty")
        return await asafe(cap.capture(data=data, name=str(payload.get("name") or "upload")[:120], channel="panel", origin="you", private=bool(payload.get("private"))))

    @api.patch("/knowledge/captures/{cid}")
    async def captures_edit(cid: str, payload: dict = Body(...)):
        kb = need("knowledge")
        fields = {}
        if "text" in payload:
            fields.update(raw=str(payload["text"]), status="pending")
        if "private" in payload:
            fields["private"] = bool(payload["private"])
        return safe(kb.update_capture, cid, **fields)

    @api.post("/knowledge/captures/{cid}/process")
    async def captures_process(cid: str):
        return await asafe(need("knowledge_capture").process(cid))

    @api.post("/knowledge/captures/{cid}/approve")
    async def captures_approve(cid: str):
        return safe(need("knowledge_capture").approve, cid, "you")

    @api.post("/knowledge/captures/{cid}/archive")
    async def captures_archive(cid: str):
        return safe(need("knowledge").update_capture, cid, status="archived")

    @api.delete("/knowledge/captures/{cid}")
    async def captures_delete(cid: str):
        if not need("knowledge").delete_capture(cid, "you"):
            raise HTTPException(status_code=404, detail="no such capture")
        return {"ok": True}

    # ---- items and links: what the owner decides -------------------------------------------------------------------------------
    @api.post("/knowledge/items")
    async def items_add(payload: dict = Body(...)):
        cap = need("knowledge_capture")
        added = safe(cap.add, str(payload.get("kind") or "note"), str(payload.get("title") or ""), str(payload.get("body") or ""), authored="you", summary=str(payload.get("summary") or ""),
                     project=str(payload.get("project") or ""), tags=payload.get("tags") or (), private=bool(payload.get("private")), actor="you")
        return {**added["item"], "duplicate": added["duplicate"]}

    @api.patch("/knowledge/items/{iid}")
    async def items_edit(iid: str, payload: dict = Body(...)):
        kb = need("knowledge")
        fields = {k: payload[k] for k in ("title", "body", "summary", "kind", "project", "tags", "private") if k in payload}
        if fields:
            safe(kb.update_item, iid, actor="you", **fields)
        if payload.get("status"):
            safe(kb.set_status, iid, str(payload["status"]), actor="you", note=str(payload.get("why") or ""))
        if "archive" in payload:
            kb.archive(iid, bool(payload["archive"]), actor="you")
        if payload.get("reviewed"):
            kb.mark_reviewed(iid, actor="you")
        got = kb.item(iid)
        if got is None:
            raise HTTPException(status_code=404, detail="no such item")
        return got

    @api.post("/knowledge/links")
    async def links_add(payload: dict = Body(...)):
        kb = need("knowledge")
        return safe(kb.link, str(payload.get("src") or ""), str(payload.get("rel") or "relates to"), str(payload.get("dst") or ""), why=str(payload.get("why") or ""), by="you")

    @api.post("/knowledge/links/{lid}/{verdict}")
    async def links_judge(lid: int, verdict: str):
        if verdict not in ("confirm", "reject"):
            raise HTTPException(status_code=404, detail="no such action")
        return safe(need("knowledge").set_link, lid, "confirmed" if verdict == "confirm" else "rejected", actor="you")
