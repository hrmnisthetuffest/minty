"""Hevy: read-only polling, backs off on errors, the key never appears in a log or an error (LF-03)."""
import json
import logging

import httpx

from minty_core.health import HealthHub
from minty_core.hevy import HevyClient, HevyError, HevyPoller

KEY = "11111111-2222-3333-4444-555555555555"


def workout(i, day=12):
    return {"id": f"w{i}", "title": f"Session {i}", "start_time": f"2026-10-{day:02d}T07:00:00+05:30",
            "end_time": f"2026-10-{day:02d}T08:00:00+05:30",
            "exercises": [{"title": "Squat", "sets": [{"index": 0, "type": "normal", "weight_kg": 80, "reps": 5}]}]}


class FakeHevy:
    def __init__(self, pages):
        self.pages, self.requests, self.responses = pages, [], []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        page = int(request.url.params.get("page", "1"))
        body = {"page": page, "page_count": len(self.pages), "workouts": self.pages[page - 1]}
        return httpx.Response(200, json=body)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def setup(tmp_path, pages, grant=True):
    hub = HealthHub(tmp_path / "health.db", clock=lambda: 1_800_000_000)
    if grant:
        hub.grant("training")
    fake = FakeHevy(pages)
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    poller = HevyPoller(hub, lambda: KEY, fake.client(), sleep=sleep, clock=lambda: 1_800_000_000)
    return hub, fake, poller, sleeps


async def test_it_reads_only_and_stops_at_what_it_already_has(tmp_path):
    hub, fake, poller, _ = setup(tmp_path, [[workout(3, 14), workout(2, 13)], [workout(1, 12)]])
    result = await poller.sync_once()
    assert result.added == 3 and result.error == "" and hub.counts()["training"] == 3
    assert {r.method for r in fake.requests} == {"GET"} and fake.requests[0].url.path == "/v1/workouts"
    assert fake.requests[0].headers["api-key"] == KEY and KEY not in str(fake.requests[0].url)
    fake.requests.clear()
    again = await poller.sync_once()
    assert again.added == 0 and len(fake.requests) == 1  # the newest page was all known: no need to read on


async def test_a_refused_key_stops_polling_and_says_so_once(tmp_path):
    hub, fake, poller, _ = setup(tmp_path, [[]])
    fake.responses.append(httpx.Response(401, json={"error": "Invalid API key"}))
    result = await poller.sync_once()
    assert result.error == "auth" and "refused the key" in result.message
    fake.requests.clear()
    assert (await poller.sync_once()).error == "paused" and fake.requests == []  # not hammering a dead key
    poller.resume()
    assert (await poller.sync_once()).error == ""


async def test_rate_limits_are_waited_out_and_server_errors_back_off(tmp_path):
    hub, fake, poller, sleeps = setup(tmp_path, [[workout(1)]])
    fake.responses.append(httpx.Response(429, headers={"retry-after": "7"}, json={}))
    result = await poller.sync_once()
    assert result.added == 1 and sleeps == [7.0]
    hub2, fake2, poller2, _ = setup(tmp_path / "b", [[workout(1)]])
    for _ in range(3):
        fake2.responses.append(httpx.Response(503, json={}))
    first = await poller2.sync_once()
    assert first.error == "server" and poller2.next_delay_s() == 600
    fake2.responses.append(httpx.Response(503, json={}))
    second = await poller2.sync_once()
    assert second.error == "server" and poller2.next_delay_s() == 1200  # doubled


async def test_the_key_is_never_in_a_log_line_or_an_error(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    hub, fake, poller, _ = setup(tmp_path, [[]])
    fake.responses.append(httpx.Response(500, text=f"boom {KEY}"))
    result = await poller.sync_once()
    assert KEY not in result.message and KEY not in caplog.text and KEY not in repr(result)
    try:
        HevyClient(KEY, fake.client())._check(httpx.Response(418, text=KEY))
    except HevyError as e:
        assert KEY not in str(e)


async def test_without_agreement_or_a_key_it_makes_no_request(tmp_path):
    hub, fake, poller, _ = setup(tmp_path, [[workout(1)]], grant=False)
    assert (await poller.sync_once()).error == "consent" and fake.requests == []
    hub.grant("training")
    keyless = HevyPoller(hub, lambda: None, fake.client())
    assert (await keyless.sync_once()).error == "nokey" and fake.requests == []


async def test_an_unexpected_reply_is_an_error_not_a_crash(tmp_path):
    hub, fake, poller, _ = setup(tmp_path, [[]])
    fake.responses.append(httpx.Response(200, json={"hello": "world"}))
    assert (await poller.sync_once()).error == "server" and hub.counts()["training"] == 0


async def test_a_different_key_lifts_the_pause_by_itself(tmp_path):
    hub, fake, _, _ = setup(tmp_path, [[workout(1)]])
    keys = {"now": KEY}
    poller = HevyPoller(hub, lambda: keys["now"], fake.client())
    fake.responses.append(httpx.Response(401, json={}))
    assert (await poller.sync_once()).error == "auth"
    assert (await poller.sync_once()).error == "paused"
    keys["now"] = "99999999-aaaa-bbbb-cccc-dddddddddddd"
    assert (await poller.sync_once()).added == 1
