"""One handle on the web helpers for the plugin, the browser plugin's hooks and the loop. Nothing is created on disk until
one of the `web.*` switches is on and something asks for it."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable

from minty_core.web.captions import fetch_captions
from minty_core.web.db import WebDB
from minty_core.web.downloads import Quarantine, VirusTotal
from minty_core.web.formlog import FormLog
from minty_core.web.readlater import ReadLater
from minty_core.web.safeopen import Opened, fetch_clean, safe_open
from minty_core.web.sessions import Sessions
from minty_core.web.steps import HandOver
from minty_core.web.watch import Watcher

OFF_WATCH = "Web watching is switched off (web.watch on the Switches page)."
OFF_DOWNLOADS = "The download quarantine is switched off (web.downloads on the Switches page)."
OFF_HELPERS = "The web helpers are switched off (web.helpers on the Switches page)."


class WebOff(RuntimeError):
    pass


async def _default_run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), 120)
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def _default_http_get(url: str, headers: dict) -> tuple[int, dict]:
    from minty_core.httpclient import get_client

    resp = await get_client().get(url, headers=headers, timeout=15.0)
    try:
        return resp.status_code, (resp.json() if resp.status_code == 200 else {})
    except ValueError:
        return resp.status_code, {}


class WebProvider:
    def __init__(self, config: Callable, directory: Path, services: dict, clock: Callable[[], float] = time.time,
                 fetch=None, http_get=None, run=None, secret: Callable[[str], str | None] = lambda name: None):
        self._config, self._dir, self._services, self._clock = config, Path(directory), services, clock
        self._fetch, self._http_get, self._run, self._secret = fetch, http_get or _default_http_get, run or _default_run, secret
        self._db: WebDB | None = None
        self._watcher: Watcher | None = None
        self._quarantine: Quarantine | None = None
        self.handover = HandOver(clock=clock)

    # ---- switches -------------------------------------------------------------------------------------
    def watch_on(self) -> bool:
        return bool(self._config().web.watch)

    def downloads_on(self) -> bool:
        return bool(self._config().web.downloads)

    def helpers_on(self) -> bool:
        return bool(self._config().web.helpers)

    def need(self, which: str) -> None:
        on, message = {"watch": (self.watch_on, OFF_WATCH), "downloads": (self.downloads_on, OFF_DOWNLOADS),
                       "helpers": (self.helpers_on, OFF_HELPERS)}[which]
        if not on():
            raise WebOff(message)

    # ---- the pieces -----------------------------------------------------------------------------------
    def _folder(self, name: str) -> Path:
        base = self._config().web.folder
        return (Path(base).expanduser() if base else self._dir) / name

    def db(self) -> WebDB:
        if self._db is None:
            self._db = WebDB(self._dir / "web.db")
        return self._db

    async def raw_fetch(self, url: str):
        """GET with no cookies in or out, DNS-pinned and size-capped (safe_fetch)."""
        if self._fetch is not None:
            return await self._fetch(url)
        return await fetch_clean(url)

    async def _watch_fetch(self, url: str) -> tuple[int, str]:
        got = await self.raw_fetch(url)
        return got.status, got.data.decode(got.charset or "utf-8", "replace")

    def watcher(self) -> Watcher:
        hours = self._config().web.min_watch_hours
        if self._watcher is None or self._watcher.min_hours != max(1.0, float(hours)):
            self._watcher = Watcher(self.db(), self._watch_fetch, clock=self._clock, min_hours=hours)
        return self._watcher

    def readlater(self) -> ReadLater:
        return ReadLater(self.db(), self._clock)

    def sessions(self) -> Sessions:
        return Sessions(self.db(), self._clock)

    def formlog(self) -> FormLog:
        return FormLog(self.db(), self._clock)

    def quarantine(self) -> Quarantine:
        if self._quarantine is None:
            vt = VirusTotal(lambda: self._secret("virustotal_key"), self._http_get, self.db(), self._clock)
            self._quarantine = Quarantine(self.db(), self._folder("quarantine"), vt, self._clock)
        return self._quarantine

    def release_folder(self) -> Path:
        return Path(self._config().web.release_folder).expanduser()

    def show_folder(self) -> Path:
        return self._folder("show")

    # ---- reading --------------------------------------------------------------------------------------
    async def open(self, url: str, excerpt: int = 3000) -> Opened:
        return await safe_open(url, self.raw_fetch, excerpt=excerpt)

    async def captions(self, url: str) -> str:
        return await fetch_captions(url, self._run, self._dir / "captions-tmp")

    # ---- hooks the browser plugin calls ---------------------------------------------------------------
    def record_field(self, host: str, field: str) -> None:
        if self.helpers_on():
            self.formlog().record(host, field)

    def ingest_download(self, path: Path, name: str, source: str) -> dict | None:
        """The browser saved a file: quarantine it if `web.downloads` is on, else leave it where it is."""
        if not self.downloads_on():
            return None
        return self.quarantine().ingest(path, source, name)

    # ---- the loop -------------------------------------------------------------------------------------
    async def tick(self) -> list[str]:
        return await self.watcher().run_due() if self.watch_on() else []
