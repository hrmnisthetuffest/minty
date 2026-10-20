"""One handle on the creation helpers for the plugin and the loop. Nothing is created on disk until `create.enabled` is on and
something asks. Every outside thing (feeds, arXiv, Cloudflare, ffmpeg, the speech recogniser) can be replaced in tests."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from minty_core.create.db import CreateDB
from minty_core.create.documents import save
from minty_core.create.feeds import News
from minty_core.create.images import ImageGen
from minty_core.create.media import Media, make_runner
from minty_core.create.papers import PaperWatch
from minty_core.create.sources import Sources, file_sha
from minty_core.create.translate import TranslationGlossary
from minty_core.dev.ideas import Ideas
from minty_core.web.urls import check_web_url

OFF = "The creation helpers are switched off (create.enabled on the Switches page)."
OFF_IMAGES = "Image making is switched off (create.images). It sends only your picture description to your Cloudflare account."


class CreateOff(RuntimeError):
    pass


async def _default_fetch(url: str) -> str:
    from minty_core.web.safeopen import fetch_clean

    got = await fetch_clean(url, max_bytes=1_500_000)
    if got.status >= 400:
        raise RuntimeError(f"HTTP {got.status}")
    return got.data.decode(got.charset or "utf-8", "replace")


async def _default_get(url: str, params: dict | None = None) -> str:
    from minty_core.httpclient import get_client
    from minty_core.safefetch import safe_fetch

    get_client().cookies.clear()
    try:
        got = await safe_fetch("GET", url, params=params, max_bytes=1_500_000)
    finally:
        get_client().cookies.clear()
    if got.status >= 400:
        raise RuntimeError(f"HTTP {got.status}")
    return got.data.decode(got.charset or "utf-8", "replace")


async def _default_post(url: str, headers: dict, body: dict):
    from minty_core.httpclient import get_client

    resp = await get_client().post(url, headers=headers, json=body, timeout=90.0)
    if resp.headers.get("content-type", "").startswith("image/"):
        return resp.status_code, resp.content
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {}


class CreateProvider:
    def __init__(self, config: Callable, directory: Path, services: dict, clock: Callable[[], float] = time.time,
                 fetch=None, get=None, post=None, runner=None, secret: Callable[[str], str | None] = lambda name: None,
                 sleep=None):
        self._config, self._dir, self._services, self._clock = config, Path(directory), services, clock
        self._fetch, self._get, self._post = fetch or _default_fetch, get or _default_get, post or _default_post
        self._runner, self._secret, self._sleep = runner, secret, sleep
        self._db: CreateDB | None = None

    def need(self, part: str = "read") -> None:
        cfg = self._config().create
        if not cfg.enabled:
            raise CreateOff(OFF)
        if part == "images" and not cfg.images:
            raise CreateOff(OFF_IMAGES)

    def db(self) -> CreateDB:
        if self._db is None:
            self._db = CreateDB(self._dir / "create.db")
        return self._db

    def folder(self) -> Path:
        return Path(self._config().create.folder).expanduser()

    def news(self) -> News:
        return News(self._fetch, self.db(), self._clock)

    def feeds(self) -> list[str]:
        with self.db().lock:
            stored = [r["url"] for r in self.db().db.execute("SELECT url FROM feeds ORDER BY added").fetchall()]
        return list(dict.fromkeys([*self._config().create.feeds, *stored]))

    def add_feed(self, url: str) -> str:
        url = check_web_url(url)
        with self.db().lock:
            self.db().db.execute("INSERT OR IGNORE INTO feeds(url, added) VALUES (?,?)", (url, self._clock()))
            self.db().db.commit()
        return url

    def remove_feed(self, url: str) -> bool:
        with self.db().lock:
            n = self.db().db.execute("DELETE FROM feeds WHERE url=?", (str(url).strip(),)).rowcount
            self.db().db.commit()
        return bool(n)

    def papers(self) -> PaperWatch:
        kw = {"sleep": self._sleep} if self._sleep else {}
        return PaperWatch(self.db(), self._get, self._clock, **kw)

    def ideas(self) -> Ideas:
        return Ideas(self.db(), self._clock)

    def glossary(self) -> TranslationGlossary:
        return TranslationGlossary(self.db())

    def sources(self) -> Sources:
        return Sources(self.db(), self._clock)

    def images(self) -> ImageGen:
        cfg = self._config().create
        return ImageGen(self._post, self._secret, self.db(), self.folder() / "images", self._clock, cfg.daily_images)

    def media(self) -> Media:
        roots = [Path(p).expanduser() for p in self._config().create.media_folders]
        return Media(self._runner or make_runner(), [r for r in roots if r.is_dir()], self.folder() / "media")

    # ---- notes made from a source -----------------------------------------------------------------------
    def save_notes(self, title: str, markdown: str, kind: str, sources: list[tuple[str, str]]) -> str:
        """Into the notes folder when it is on (as outside text, so it stays in sources/), else a Markdown file; the sources
        are recorded either way so a removed source flags the note."""
        vault = self._services.get("vault")
        if vault is not None and vault.active():
            note = vault.kit().vault.create(title, markdown, folder="sources", origin="file", tags=["show-notes", "untrusted-speech"],
                                            source=kind)
            ref = note.id
        else:
            ref = str(save(self.folder() / "notes", title, markdown, "md"))
        self.sources().record(ref, title, kind, sources)
        return ref

    def source_of(self, path: Path) -> tuple[str, str]:
        return str(path), file_sha(path)

    # ---- the loop -------------------------------------------------------------------------------------
    async def tick(self, now: datetime | None = None) -> list[str]:
        cfg = self._config().create
        if not cfg.enabled:
            return []
        now = now or datetime.now()
        out: list[str] = []
        if cfg.paper_push and self.papers().topics():
            out += await self.papers().check()
        if cfg.news_push and (self.feeds()):
            h, m = (int(x) for x in cfg.news_time.split(":"))
            start = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if start <= now <= start + timedelta(minutes=30) and self.db().meta("news_sent") != now.date().isoformat():
                self.db().set_meta("news_sent", now.date().isoformat())
                out.append(await self.news().brief(self.feeds()))
        return out
