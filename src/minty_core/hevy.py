"""Hevy, read-only: new workouts every few hours into the health hub.

Only GET requests exist here, so the key (which Hevy issues account-wide, Pro plans only) is never used to change
anything. The key goes in the `api-key` header, never in a URL, a log line or an error. A refused key pauses polling
until you resume it; rate limits are waited out; server errors back off, doubling up to six hours.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Callable

import httpx

from minty_core.health import HealthHub
from minty_core.health_import import _hevy_json, store_records

log = logging.getLogger(__name__)

BASE = "https://api.hevyapp.com"
PAGE_SIZE = 10
MAX_PAGES = 10
FIRST_BACKOFF_S = 600
MAX_BACKOFF_S = 6 * 3600


class HevyError(Exception):
    def __init__(self, kind: str, message: str = ""):
        super().__init__(message or kind)
        self.kind = kind


class HevyClient:
    def __init__(self, key: str, client: httpx.AsyncClient, base: str = BASE, sleep=asyncio.sleep,
                 max_retries: int = 2):
        self._key, self._client, self._base = key, client, base.rstrip("/")
        self._sleep, self._max_retries = sleep, max_retries

    @staticmethod
    def _check(resp: httpx.Response) -> dict:
        """The JSON body, or a HevyError built from the status alone (the body is never quoted)."""
        if resp.status_code in (401, 403):
            raise HevyError("auth", "Hevy refused the key")
        if resp.status_code == 429:
            raise HevyError("rate", "Hevy asked me to slow down")
        if resp.status_code != 200:
            raise HevyError("server", f"Hevy answered with an error (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError:
            raise HevyError("server", "Hevy sent something I couldn't read") from None
        if not isinstance(body, dict):
            raise HevyError("server", "Hevy sent an unexpected reply")
        return body

    async def workouts(self, page: int = 1, page_size: int = PAGE_SIZE) -> dict:
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.get(f"{self._base}/v1/workouts", params={"page": page, "pageSize": page_size},
                                              headers={"api-key": self._key}, timeout=20.0)
            except httpx.HTTPError as e:  # the exception text can carry the request: say only its class
                raise HevyError("network", f"could not reach Hevy ({type(e).__name__})") from None
            if resp.status_code == 429 and attempt < self._max_retries:
                try:
                    wait = min(float(resp.headers.get("retry-after", "5")), 60.0)
                except ValueError:
                    wait = 5.0
                await self._sleep(wait)
                continue
            return self._check(resp)
        raise HevyError("rate", "Hevy asked me to slow down")


@dataclass
class SyncResult:
    added: int = 0
    pages: int = 0
    error: str = ""  # "", auth, paused, server, network, rate, consent, nokey
    message: str = ""


class HevyPoller:
    def __init__(self, hub: HealthHub, key: Callable[[], str | None], client: httpx.AsyncClient, *,
                 poll_s: float = 6 * 3600, sleep=asyncio.sleep, clock=None, base: str = BASE):
        self.hub, self._key, self._client, self.poll_s = hub, key, client, poll_s
        self._sleep, self._base = sleep, base
        self._fails = 0
        self._paused = False
        self._refused: str | None = None  # the key that was refused: a different one lifts the pause by itself

    def resume(self) -> None:
        self._paused, self._fails = False, 0

    def next_delay_s(self) -> float:
        if not self._fails:
            return self.poll_s
        return min(FIRST_BACKOFF_S * 2 ** (self._fails - 1), MAX_BACKOFF_S)

    async def sync_once(self) -> SyncResult:
        if self._paused and self._key() not in (None, self._refused):
            self.resume()
        if self._paused:
            return SyncResult(error="paused", message="Hevy polling is paused until you set a new key.")
        if not self.hub.consent("training"):
            return SyncResult(error="consent", message="You haven't agreed to collecting training data.")
        key = self._key()
        if not key:
            return SyncResult(error="nokey", message="No Hevy key yet. Run: minty set-key hevy")
        api = HevyClient(key, self._client, self._base, self._sleep)
        result = SyncResult()
        try:
            for page in range(1, MAX_PAGES + 1):
                body = await api.workouts(page)
                workouts = body.get("workouts")
                if not isinstance(workouts, list):
                    raise HevyError("server", "Hevy sent an unexpected reply")
                records, _bad = _hevy_json(json.dumps({"workouts": workouts}))
                report = store_records(self.hub, "hevy", records)
                result.pages += 1
                result.added += report.added
                if report.added == 0 or page >= int(body.get("page_count") or 1):
                    break  # a page of nothing new: everything older is already here
        except HevyError as e:
            if e.kind == "auth":
                self._paused, self._refused = True, key
                result.message = ("Hevy refused the key (a Hevy Pro account is needed for the API). I've stopped "
                                  "asking; set a new key with: minty set-key hevy")
            else:
                self._fails += 1
                result.message = f"{e} — I'll try again later."
            result.error = e.kind
            log.warning("hevy sync: %s", e.kind)
            return result
        self._fails = 0
        return result
