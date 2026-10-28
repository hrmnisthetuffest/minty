"""What the panel's studio page asks the core: the catalogue, the clips to load ahead, a shuffle, preferences, and the clips and poster
frames themselves. A clip can be fetched only if it is in the catalogue, ready, and inside the library folder; no file path leaves the core.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from minty_core.studio import media
from minty_core.studio.catalog import CATEGORIES

MAX_AHEAD = 3
LISTED = 60
CACHE = {"Cache-Control": "private, max-age=86400"}  # a clip never changes under its number; private: not for a shared cache


class PreferBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field("", max_length=40)
    asset: str = Field("", max_length=12)
    choice: str = Field(max_length=20)


def add_routes(api, ctx) -> None:
    thumb_lock = asyncio.Lock()

    def player():
        p = ctx.services.get("studio_player")
        if p is None:
            raise HTTPException(status_code=404, detail="the studio is not on")
        return p

    def credit(asset) -> str:
        return asset.attribution or ""

    def shown(choice) -> dict:
        return {"id": choice.asset.id, "category": choice.category, "credit": credit(choice.asset), "reason": choice.reason}

    def playable(p, clip_id: int) -> Path:
        clip = next((c for c in p.catalog.clips() if c.asset.id == clip_id), None)
        if clip is None:
            raise HTTPException(status_code=404, detail="no such clip")
        path = Path(clip.asset.path).resolve()
        try:
            path.relative_to(Path(p.lib.root).resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="no such clip") from None
        if not path.is_file():
            raise HTTPException(status_code=404, detail="that clip was removed")
        return path

    @api.get("/studio/catalog")
    async def studio_catalog():
        p = player()
        prefs, counts = p.catalog.prefs(), p.catalog.counts()
        styles = [{"key": k, "label": c.label, "count": counts.get(k, 0), "own_only": not c.queries,
                   "off": k in prefs["exclude_categories"] or prefs["weights"].get(k) == 0, "weight": prefs["weights"].get(k, 1.0)}
                  for k, c in CATEGORIES.items()]
        ahead = p.catalog.peek(1, now=p.now())
        favourites, skipped = set(prefs["favourites"]), set(prefs["exclude_assets"])
        clips = [{"id": c.asset.id, "category": c.category, "credit": credit(c.asset), "note": c.asset.note, "provider": c.provider,
                  "favourite": c.asset.id in favourites, "skipped": c.asset.id in skipped} for c in p.catalog.clips()[:LISTED]]
        return {"styles": styles, "mode": prefs["mode"], "today": shown(ahead[0]) if ahead else None, "clips": clips}

    @api.get("/studio/next")
    async def studio_next(count: int = Query(2, ge=1)):
        p = player()
        return [shown(c) for c in p.catalog.peek(min(count, MAX_AHEAD), now=p.now())]

    @api.post("/studio/shuffle")
    async def studio_shuffle():
        p = player()
        choice = p.catalog.shuffle(now=p.now())
        if choice is None:
            raise HTTPException(status_code=409, detail="there are no clips in the catalogue yet")
        return shown(choice)

    @api.post("/studio/prefer")
    async def studio_prefer(body: PreferBody):
        p = player()
        message = p.prefer({k: v for k, v in (("category", body.category), ("asset", body.asset), ("choice", body.choice)) if v})
        if message.startswith("Error"):
            raise HTTPException(status_code=422, detail=message.removeprefix("Error:").strip())
        return {"message": message}

    @api.get("/studio/clip/{clip_id}")
    async def studio_clip(clip_id: int):
        path = playable(player(), clip_id)
        return FileResponse(path, media_type="video/mp4", headers=CACHE)  # Range requests are answered by FileResponse

    @api.get("/studio/thumb/{clip_id}")
    async def studio_thumb(clip_id: int):
        p = player()
        src = playable(p, clip_id)
        thumb = Path(p.thumbs) / f"{clip_id}.jpg"
        if not thumb.is_file():
            async with thumb_lock:
                if not thumb.is_file():
                    try:
                        await media.poster(src, thumb, 360)
                    except media.MediaError:
                        raise HTTPException(status_code=404, detail="no picture for that clip") from None
        return FileResponse(thumb, media_type="image/jpeg", headers=CACHE)
