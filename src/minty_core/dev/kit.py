"""One handle on the developer helpers for the plugin and the CI loop. Nothing is created on disk until `dev.enabled` is on and
something asks. Every outside thing (git, tests, gh, OSV, the clock) can be replaced in tests."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from minty_core.dev import ci as ci_mod
from minty_core.dev.git import Git, make_runner as make_git_runner
from minty_core.dev.ideas import Ideas
from minty_core.dev.osv import Osv
from minty_core.dev.projects import Projects
from minty_core.dev.snippets import DevDB, Snippets
from minty_core.dev.testrun import make_runner as make_test_runner

OFF = "The developer helpers are switched off (dev.enabled on the Switches page)."
OFF_OSV = "The dependency radar is switched off (dev.osv). It sends package names and versions, nothing else, to OSV.dev."
OFF_REVIEW = "Branch review is switched off (dev.review). It uses your Claude plan and sends the diff to Anthropic."


class DevOff(RuntimeError):
    pass


async def _default_post(url: str, body: dict) -> tuple[int, dict]:
    from minty_core.httpclient import get_client

    resp = await get_client().post(url, json=body, timeout=30.0)
    try:
        return resp.status_code, (resp.json() if resp.status_code == 200 else {})
    except ValueError:
        return resp.status_code, {}


async def _default_get(url: str) -> tuple[int, dict]:
    from minty_core.httpclient import get_client

    resp = await get_client().get(url, timeout=30.0)
    try:
        return resp.status_code, (resp.json() if resp.status_code == 200 else {})
    except ValueError:
        return resp.status_code, {}


class DevProvider:
    def __init__(self, config: Callable, directory: Path, services: dict, clock: Callable[[], float] = time.time,
                 git_runner=None, test_runner=None, gh_runner=None, post=None, get=None, sleep=None):
        self._config, self._dir, self._services, self._clock = config, Path(directory), services, clock
        self._git_runner, self._test_runner, self._gh_runner = git_runner, test_runner, gh_runner
        self._post, self._get, self._sleep = post or _default_post, get or _default_get, sleep
        self._db: DevDB | None = None
        self._ci: ci_mod.CiWatcher | None = None
        self._osv: Osv | None = None

    def need(self, part: str = "read") -> None:
        cfg = self._config().dev
        ok, msg = {"read": (cfg.enabled, OFF), "osv": (cfg.enabled and cfg.osv, OFF_OSV),
                   "review": (cfg.enabled and cfg.review, OFF_REVIEW)}[part]
        if not ok:
            raise DevOff(msg)

    def projects(self) -> Projects:
        cfg = self._config().dev
        return Projects(cfg.root, cfg.projects)

    def git(self) -> Git:
        return Git(self._git_runner or make_git_runner())

    def test_runner(self):
        return self._test_runner or make_test_runner(self._config().dev.test_timeout_s)

    def db(self) -> DevDB:
        if self._db is None:
            self._db = DevDB(self._dir / "dev.db")
        return self._db

    def snippets(self) -> Snippets:
        return Snippets(self.db(), self._clock)

    def ideas(self) -> Ideas:
        return Ideas(self.db(), self._clock)

    def osv(self) -> Osv:
        if self._osv is None:
            kw = {"sleep": self._sleep} if self._sleep else {}
            self._osv = Osv(self._post, self._get, self._dir / "dev-osv-cache.json", self._clock, **kw)
        return self._osv

    def gh(self):
        return self._gh_runner or ci_mod.make_runner()

    def ci(self) -> ci_mod.CiWatcher:
        cfg = self._config().dev
        if self._ci is None:
            self._ci = ci_mod.CiWatcher(self.gh(), self._clock, cfg.ci_minutes * 60.0)
        self._ci.min_gap = max(300.0, cfg.ci_minutes * 60.0)
        return self._ci

    def trace_dir(self) -> Path:
        return self._dir / "traces"

    async def tick(self) -> list[str]:
        cfg = self._config().dev
        if not (cfg.enabled and cfg.ci_watch and cfg.ci_projects):
            return []
        out: list[str] = []
        for name in cfg.ci_projects:
            try:
                out += await self.ci().poll(self.projects().resolve(name), name)
            except ValueError:
                continue
        return out
