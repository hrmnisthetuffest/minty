import asyncio
import hmac
import json
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from minty_core.commands import CommandRouter  # noqa: F401  (re-exported for callers and tests)
from minty_core.typed import command_router
from minty_core.context import AppContext
from minty_core.keystore import SecretsDamaged
from minty_core.llm.types import LLMError
from minty_core.trace import turn_scope

security = logging.getLogger("minty.security")


class SecretBody(BaseModel):
    value: str = Field(min_length=1)


class TextBody(BaseModel):
    text: str


class FlagBody(BaseModel):
    path: str
    value: StrictBool  # only a real true/false: "yes" or 1 is refused


class MuteBody(BaseModel):
    muted: bool


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Literal["fast", "deep"] | None = None
    private: StrictBool | None = None


class PluginBody(BaseModel):
    enabled: bool | None = None
    settings: dict[str, Any] | None = None


def create_app(ctx: AppContext) -> FastAPI:
    if not ctx.token:
        raise ValueError("empty API token")
    app = FastAPI(title="minty-core")

    def require_token(authorization: str = Header(default="")) -> None:
        supplied = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), ctx.token.encode()):
            raise HTTPException(status_code=401, detail="invalid token")

    api = APIRouter(dependencies=[Depends(require_token)])

    def need_pipeline():
        if ctx.pipeline is None:
            raise HTTPException(status_code=503, detail="voice pipeline not running")
        return ctx.pipeline

    @api.get("/jobs")
    async def get_jobs():
        runner = ctx.services.get("jobs")
        if runner is None:
            return []
        now = time.time()
        return [{"id": j.id, "kind": j.kind, "label": j.label(), "goal": j.goal, "status": j.status,
                 "progress": j.progress[-3:], "question": j.question, "result": j.result[:400],
                 "seconds": int(j.elapsed(now))} for j in reversed(runner.select()[-20:])]

    @api.post("/jobs/{job_id}/cancel")
    async def cancel_job(job_id: int):
        runner = ctx.services.get("jobs")
        return {"cancelled": bool(runner is not None and await runner.cancel(job_id))}

    @api.get("/receipts")
    async def get_receipts():
        store = ctx.services.get("receipts")
        if store is None:
            return []
        return [{"id": r.id, "ts": r.ts, "tool": r.tool, "risk": r.risk, "summary": r.summary, "request": r.request,
                 "approval_id": r.approval_id, "outcome": r.outcome, "verified": r.verified, "detail": r.detail}
                for r in store.recent(50)]

    @api.get("/timeline")
    async def get_timeline():
        from minty_core.selfcheck.timeline import build_timeline

        runner, store = ctx.services.get("jobs"), ctx.services.get("receipts")
        return build_timeline(runner.select()[-60:] if runner is not None else [], store.recent(60) if store is not None else [])

    @api.get("/destinations")
    async def get_destinations():
        m = ctx.services.get("destinations")
        return {p: [{"host": h, "count": n} for h, n in hosts] for p, hosts in (m.listing() if m else {}).items()}

    @api.get("/flags")
    async def get_flags():
        from minty_core.flags import list_flags
        return list_flags(ctx.store.config)

    @api.post("/flags")
    async def set_flag_route(body: FlagBody):
        from minty_core.flags import set_flag
        try:
            known = set_flag(ctx.store, body.path, body.value)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if not known:
            raise HTTPException(status_code=404, detail="no such switch")
        return {"path": body.path, "value": body.value}

    @api.get("/lockdown")
    async def get_lockdown():
        lock = ctx.services.get("lockdown")
        return lock.state() if lock is not None else {"active": False, "reason": "", "since": 0}

    @api.post("/lockdown")
    async def engage_lockdown():
        lock = ctx.services.get("lockdown")
        if lock is not None:
            lock.engage("api")
        return await get_lockdown()

    @api.post("/lockdown/release")
    async def release_lockdown():
        lock = ctx.services.get("lockdown")
        if lock is not None:
            lock.release("api")
        return await get_lockdown()

    @api.get("/approvals")
    async def get_approvals():
        store = ctx.services.get("approvals")
        if store is None:
            return []
        now = time.time()
        return [{"id": a.id, "tool": a.tool, "question": a.question, "risk": a.risk, "job_id": a.job_id,
                 "expires_in": max(0, int(a.expires - now))} for a in store.pending()]  # never the arguments

    @api.post("/approvals/{approval_id}/approve")
    async def approve(approval_id: int):
        store = ctx.services.get("approvals")
        return {"ok": bool(store is not None and store.decide(approval_id, True, via="panel"))}

    @api.post("/approvals/{approval_id}/deny")
    async def deny(approval_id: int):
        store = ctx.services.get("approvals")
        return {"ok": bool(store is not None and store.decide(approval_id, False, via="panel"))}

    @api.get("/status")
    async def status():
        pipe = ctx.pipeline
        return {
            "state": pipe.state if pipe else "offline",
            "muted": pipe.muted if pipe else False,
            "providers": ctx.router.status(),
        }

    @api.get("/config")
    async def get_config():
        return ctx.store.config.model_dump(mode="json")

    @api.put("/config")
    async def put_config(body: dict[str, Any] = Body(...)):
        try:
            return ctx.store.update(body).model_dump(mode="json")
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=json.loads(e.json()))

    @api.get("/secrets")
    async def get_secrets():
        return ctx.secrets.masked()

    @api.put("/secrets/{name}", status_code=204)
    async def put_secret(name: str, body: SecretBody):
        try:
            ctx.secrets.set(name, body.value)
        except SecretsDamaged as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return Response(status_code=204)

    @api.delete("/secrets/{name}", status_code=204)
    async def delete_secret(name: str):
        try:
            ctx.secrets.delete(name)
        except SecretsDamaged as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return Response(status_code=204)

    @api.get("/personalities")
    async def list_personalities():
        return ctx.personalities.list()

    @api.get("/personalities/{name}")
    async def get_personality(name: str):
        try:
            return {"name": name, "text": ctx.personalities.get(name)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="no such personality")

    @api.put("/personalities/{name}", status_code=204)
    async def put_personality(name: str, body: TextBody):
        try:
            ctx.personalities.save(name, body.text)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return Response(status_code=204)

    @api.delete("/personalities/{name}", status_code=204)
    async def delete_personality(name: str):
        try:
            ctx.personalities.delete(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="no such personality")
        return Response(status_code=204)

    @api.get("/plugins")
    async def list_plugins():
        return ctx.host.list_plugins()

    @api.put("/plugins/{name}")
    async def put_plugin(name: str, body: PluginBody):
        data = ctx.store.config.model_dump(mode="json")
        entry = dict(data["plugins"].get(name, {}))
        if body.enabled is not None:
            entry["enabled"] = body.enabled
        if body.settings:
            entry.update(body.settings)
        data["plugins"][name] = entry
        try:
            ctx.store.update(data)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=json.loads(e.json()))
        return ctx.host.list_plugins()

    @api.get("/memory")
    async def get_memory():
        return {"notes": ctx.memory.list()}

    @api.delete("/memory/{index}", status_code=204)
    async def delete_memory(index: int):
        try:
            ctx.memory.delete(index)
        except IndexError:
            raise HTTPException(status_code=404, detail="no such note")
        return Response(status_code=204)

    class InstructionsBody(BaseModel):
        text: str

    @api.get("/prompts")
    async def get_prompts():
        layers = ctx.services["prompts"]
        return {"instructions": layers.instructions(), "learned": layers.rules(), "commands": layers.commands()}

    @api.put("/prompts/instructions", status_code=204)
    async def put_instructions(body: InstructionsBody):
        try:
            ctx.services["prompts"].set_instructions(body.text)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return Response(status_code=204)

    @api.delete("/prompts/learned/{ident}", status_code=204)
    async def delete_learned(ident: str):
        if not ctx.services["prompts"].forget_rule(ident):
            raise HTTPException(status_code=404, detail="no such rule")
        return Response(status_code=204)

    @api.delete("/prompts/commands", status_code=204)
    async def delete_command(phrase: str):
        if not ctx.services["prompts"].forget_command(phrase):
            raise HTTPException(status_code=404, detail="no such command")
        return Response(status_code=204)

    @api.post("/restart")
    async def restart_conversation():
        if ctx.agent._lock.locked():
            raise HTTPException(status_code=409, detail="Minty is answering right now; try again in a moment")
        return {"saved_messages": ctx.agent.reset_conversation()}

    @api.post("/restart-app", status_code=202)
    async def restart_app():
        restart = ctx.services.get("restart")
        if restart is None:
            raise HTTPException(status_code=503, detail="restart is not available here")
        asyncio.get_running_loop().call_later(0.4, restart)  # let this response go out first
        return {"restarting": True}

    @api.post("/mute", status_code=204)
    async def mute(body: MuteBody):
        need_pipeline().muted = body.muted
        return Response(status_code=204)

    @api.post("/listen", status_code=204)
    async def listen():
        if not need_pipeline().listen_now():
            raise HTTPException(status_code=409, detail="nothing to start: the microphone is muted or it is already listening")
        return Response(status_code=204)

    @api.post("/say", status_code=204)
    async def say(body: TextBody):
        await need_pipeline().say(body.text)
        return Response(status_code=204)

    # ---- the notes vault: the viewer/editor's routes (no delete; archive moves a note aside) ----------
    class VaultNoteBody(BaseModel):
        title: str | None = None
        text: str | None = None
        folder: str = "inbox"
        tags: list[str] | None = None
        project: str | None = None

    def vault_kit():
        from minty_core.vault.kit import VaultOff

        try:
            return ctx.services["vault"].kit()
        except VaultOff as e:
            raise HTTPException(status_code=503, detail=str(e))

    def note_json(kit, note, full: bool = False) -> dict:
        from minty_core.vault.search import badge

        out = {"id": note.id, "title": note.title, "path": note.path, "folder": note.folder, "tags": note.tags,
               "project": note.project, "confidence": note.confidence, "badge": badge(note),
               "created": note.created, "updated": note.updated, "source": note.source}
        if full:
            out["body"] = note.body
            out["warning"] = kit.search.freshness(note)
            out["backlinks"] = [{"id": n.id, "title": n.title} for n in kit.vault.backlinks(note.id)]
        return out

    def vault_error(e: Exception):
        from minty_core.vault.bookmarks import BookmarkError

        return HTTPException(status_code=422, detail=str(e)) if isinstance(e, (ValueError, BookmarkError)) else e

    @api.get("/vault")
    async def vault_overview():
        kit = vault_kit()
        kit.pipeline.propose()
        return {"status": kit.vault.status(),
                "recent": [note_json(kit, n) for n in kit.vault.recent(20)],
                "inbox": [note_json(kit, n) for n in kit.vault.list("inbox", limit=50)],
                "proposals": [p.__dict__ for p in kit.pipeline.pending()]}

    @api.get("/vault/search")
    async def vault_search(q: str = "", since: str | None = None, until: str | None = None):
        kit = vault_kit()
        hits = kit.search.where(q, since=since or None, until=until or None, limit=20)
        return {"hits": [h.__dict__ for h in hits]}

    @api.get("/vault/notes/{note_id}")
    async def vault_get(note_id: str):
        kit = vault_kit()
        note = kit.vault.get(note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="no such note")
        return note_json(kit, note, full=True)

    @api.post("/vault/notes", status_code=201)
    async def vault_create(body: VaultNoteBody):
        kit = vault_kit()
        try:
            note = kit.vault.create(body.title or "", body.text or "", folder=body.folder, origin="user",
                                    tags=body.tags or [], project=body.project or "", source="viewer")
        except ValueError as e:
            raise vault_error(e)
        return note_json(kit, note, full=True)

    @api.put("/vault/notes/{note_id}")
    async def vault_put(note_id: str, body: VaultNoteBody):
        kit = vault_kit()
        try:
            note = kit.vault.update(note_id, body=body.text, title=body.title, tags=body.tags, project=body.project,
                                    origin="user")
        except ValueError as e:
            if "No note" in str(e):
                raise HTTPException(status_code=404, detail=str(e))
            raise vault_error(e)
        return note_json(kit, note, full=True)

    @api.post("/vault/notes/{note_id}/archive")
    async def vault_archive(note_id: str):
        kit = vault_kit()
        try:
            return note_json(kit, kit.vault.archive(note_id))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @api.post("/vault/inbox/{note_id}/approve")
    async def vault_approve(note_id: str):
        kit = vault_kit()
        try:
            return note_json(kit, kit.pipeline.approve(note_id))
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @api.post("/vault/inbox/{note_id}/reject", status_code=204)
    async def vault_reject(note_id: str):
        vault_kit().pipeline.reject(note_id)
        return Response(status_code=204)

    # ---- the profile: 500 fixed questions, your answers stored on this computer ----------------------
    def profile_store():
        return ctx.services["profile"]

    def profile_summary() -> dict:
        from minty_core.profile import CATEGORIES, QUESTIONS

        counts = {c.id: sum(1 for q in QUESTIONS if q.category == c.id) for c in CATEGORIES}
        return {"categories": [{"id": c.id, "title": c.title, "blurb": c.blurb, "count": counts[c.id]} for c in CATEGORIES]}

    @api.get("/profile")
    async def profile_list():
        store = profile_store()
        return {**profile_summary(), "progress": store.progress(), "questions": store.rows()}

    @api.get("/profile/export")
    async def profile_export():
        return Response(profile_store().export_markdown(), media_type="text/markdown; charset=utf-8")

    @api.put("/profile/{number}")
    async def profile_answer(number: int, body: TextBody):
        store = profile_store()
        try:
            store.answer(number, body.text)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such question")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"id": number, "answer": store.get(number), "progress": store.progress()}

    @api.post("/profile/{number}/skip")
    async def profile_skip(number: int):
        store = profile_store()
        try:
            store.skip(number)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such question")
        return {"id": number, "progress": store.progress()}

    @api.delete("/profile/{number}", status_code=204)
    async def profile_clear(number: int):
        profile_store().clear(number)
        return Response(status_code=204)

    # ---- the memory audit: what is known, where from, and a way to correct or drop it -----------------
    class AuditBody(BaseModel):
        text: str

    def memory_audit():
        from minty_core.governance.audit import MemoryAudit

        return MemoryAudit(ctx.memory, ctx.services.get("gists"), ctx.services.get("vault"))

    @api.get("/audit")
    async def audit_list(query: str = "", kind: str = "facts"):
        rows = memory_audit().entries(kind="all" if kind == "all" else "facts", query=query, limit=200)
        return {"entries": [r.__dict__ for r in rows]}

    @api.put("/audit/{ident}")
    async def audit_edit(ident: str, body: AuditBody):
        try:
            return memory_audit().edit(ident, body.text).__dict__
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @api.delete("/audit/{ident}", status_code=204)
    async def audit_forget(ident: str):
        if not memory_audit().delete(ident):
            raise HTTPException(status_code=404, detail="no such entry")
        return Response(status_code=204)

    # ---- the encrypted private area: the passphrase is typed here, never into a chat ----------------
    class PassphraseBody(BaseModel):
        passphrase: str = Field(min_length=1)

    def private_area():
        if not ctx.store.config.vault.encryption:
            raise HTTPException(status_code=503, detail="The private notes are switched off (vault.encryption).")
        return vault_kit().private

    @api.get("/vault/private")
    async def private_status():
        area = private_area()
        notes = []
        if area.exists() and area.unlocked:
            notes = [{"id": i, "title": t} for i, t, _ in area.list()]
        return {"exists": area.exists(), "unlocked": area.exists() and area.unlocked, "notes": notes}

    @api.post("/vault/private/unlock")
    async def private_unlock(body: PassphraseBody):
        from minty_core.vault.private import PrivateError, WrongPassphrase

        area = private_area()
        try:
            area.unlock(body.passphrase)
        except WrongPassphrase:
            raise HTTPException(status_code=401, detail="That is not the passphrase.")
        except PrivateError as e:
            raise HTTPException(status_code=429, detail=str(e))
        return {"unlocked": True}

    @api.post("/vault/private/lock", status_code=204)
    async def private_lock():
        private_area().lock()
        return Response(status_code=204)

    @api.post("/vault/private/notes", status_code=201)
    async def private_add(body: VaultNoteBody):
        from minty_core.vault.private import Locked, PrivateError

        area = private_area()
        try:
            ident = area.add(body.title or "", body.text or "", body.tags or [])
        except Locked as e:
            raise HTTPException(status_code=423, detail=str(e))
        except PrivateError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"id": ident}

    @api.get("/vault/private/notes/{note_id}")
    async def private_get(note_id: str):
        from minty_core.vault.private import Locked, PrivateError

        area = private_area()
        try:
            return area.get(note_id)
        except Locked as e:
            raise HTTPException(status_code=423, detail=str(e))
        except PrivateError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @api.get("/overlay")
    async def overlay():
        """What the HUD needs in one small poll: the live state and caption, the latest notice, running timers."""
        feed = ctx.services.get("overlay_feed")
        snap = feed.snapshot() if feed is not None else {}
        store = ctx.services.get("timers")
        now = store.clock() if store is not None else 0
        snap["timers"] = [{"label": t.label, "kind": t.kind, "remaining_s": round(t.remaining(now), 1)}
                          for t in (store.all() if store is not None else [])]
        recorder = ctx.services.get("recorder")
        if recorder is not None and recorder.recording:  # the visible banner: a red REC with the running time
            snap["timers"].insert(0, {"label": "● REC", "kind": "recording", "remaining_s": round(recorder.elapsed(), 1)})
        return snap

    @api.get("/timers")
    async def timers():
        """Running timers with their due time, for the overlay."""
        store = ctx.services.get("timers")
        if store is None:
            return []
        now = store.clock()
        return [{"id": t.id, "label": t.label, "kind": t.kind, "due": t.due, "repeat_s": t.repeat_s,
                 "remaining_s": round(t.remaining(now), 1)} for t in store.all()]

    @api.post("/ptt/{action}", status_code=204)
    async def ptt(action: str):
        """Hold-to-talk from the panel or a phone: press, then release. Only does anything while push-to-talk is on."""
        controller = ctx.services.get("ptt")
        if controller is None or action not in ("press", "release"):
            raise HTTPException(404)
        getattr(controller, action)()
        return Response(status_code=204)

    @api.get("/mode")
    async def get_mode():
        return {"tier": ctx.agent.tier, "private": ctx.agent.force_private}

    @api.put("/mode")
    async def put_mode(body: ModeBody):
        if body.tier is not None:
            ctx.agent.tier = body.tier
        if body.private is not None:
            ctx.agent.force_private = body.private
        return {"tier": ctx.agent.tier, "private": ctx.agent.force_private}

    @api.post("/ask")
    async def ask(body: TextBody):
        reply = command_router(ctx).handle(body.text)
        if reply is not None:
            return {"reply": reply}
        started = time.perf_counter()
        tracer = ctx.services.get("tracer")
        outcome = "error"
        with turn_scope():
            try:
                sentences = [s async for s in ctx.agent.respond(body.text)]
                outcome = "answered"
            except LLMError as e:
                raise HTTPException(status_code=502, detail=str(e))
            finally:
                if tracer is not None:
                    tracer.event("turn", mode="api", outcome=outcome, words=len(body.text.split()),
                                 total_ms=round((time.perf_counter() - started) * 1000, 1))
        return {"reply": " ".join(sentences)}

    @api.get("/logs")
    async def get_logs(level: str = "INFO", limit: int = 200, name: str = ""):
        """name: only one source, e.g. minty.security (refused logins, lockouts, refused sockets, denied requests)."""
        return ctx.logs.recent(limit=max(1, min(limit, 500)), min_level=level, name=name[:80]) if ctx.logs else []

    from minty_core.studio.player_api import add_routes as studio_routes

    studio_routes(api, ctx)
    app.include_router(api)

    @app.websocket("/events")
    async def events(ws: WebSocket):
        # The token travels in a header, never in the address: an address is written to logs (uvicorn logs it) and
        # to every proxy and history it passes. A token in the query string is refused even when it is right.
        supplied = ws.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), ctx.token.encode()):
            security.warning("events socket refused: %s from %s", "token in the address, not a header" if ws.query_params.get("token")
                             else "missing or wrong token", ws.client.host if ws.client else "unknown")
            await ws.close(code=4401)
            return
        await ws.accept()
        queue = ctx.bus.subscribe()

        async def send() -> None:
            await ws.send_json({"type": "hello"})
            while True:
                await ws.send_json(await queue.get())

        async def watch() -> None:  # nothing is expected from the client: reading tells us when it has gone
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass

        tasks = [asyncio.create_task(send()), asyncio.create_task(watch())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)  # WebSocketDisconnect / a closed socket end here
            ctx.bus.unsubscribe(queue)

    return app
