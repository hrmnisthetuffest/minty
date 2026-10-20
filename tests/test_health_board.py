"""Provider health board, start-up key probe, self-check line and latency alarm (W0-14)."""
import asyncio

import httpx

from tests.test_pipeline import build, finish, wait_until
from tests.test_router_health import demote_a, fail, make, OK
from tests.test_router import Clock
from tests.test_turn_deadline import DelayedAgent, set_turn
from minty_core.context import build_context
from minty_core.statusline import selfcheck_line, snapshot
from minty_core.tui import TuiModel, status_items


def mock_client(routes: dict):
    def handler(req: httpx.Request):
        action = routes[req.url.host]
        if isinstance(action, Exception):
            raise action
        return httpx.Response(action, json={"data": []})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_key_probe_reports_rejected_keys_and_rests_them(tmp_path):
    router, calls = make(tmp_path, [("groq", {"base_url": "https://api.groq.test/v1"}),
                                    ("mistral", {"base_url": "https://api.mistral.test/v1"}),
                                    ("nvidia", {"base_url": "https://nim.test/v1"})],
                         {"groq": [OK], "mistral": [OK], "nvidia": [OK]})
    client = mock_client({"api.groq.test": 200, "api.mistral.test": 401,
                          "nim.test": httpx.ConnectError("down")})
    results = await router.probe(client)
    assert results == {"groq": "ok", "mistral": "key rejected", "nvidia": "unreachable"}
    rows = {r["name"]: r for r in router.status()}
    assert rows["mistral"]["probe"] == "key rejected" and rows["mistral"]["cooling_for"] >= 3000
    assert rows["nvidia"]["cooling_for"] == 0.0  # unreachable once may be the network: not rested
    calls.clear()
    [_ async for _ in router.stream([], [])]
    assert "mistral" not in calls


async def test_probe_sends_the_key_only_to_the_provider_itself(tmp_path):
    seen = []

    def handler(req):
        seen.append((req.url.host, req.url.path, req.headers.get("authorization")))
        return httpx.Response(200, json={"data": []})

    router, _ = make(tmp_path, [("groq", {"base_url": "https://api.groq.test/openai/v1"})], {"groq": [OK]})
    await router.probe(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert seen == [("api.groq.test", "/openai/v1/models", "Bearer key-groq")]


async def test_board_shows_resting_demoted_and_rejected_providers(tmp_path):
    clock = Clock()
    router, _ = make(tmp_path, [("a", {}), ("b", {})], {"a": [fail()], "b": [OK]}, clock)
    await demote_a(router, clock)
    st = {"brain_ready": 1, "brain_total": 2, "cooling": [], "health": [
        {"name": "a", "state": "demoted"}, {"name": "b", "state": "ok"}, {"name": "c", "state": "key rejected"}]}
    brain = dict((label, value) for label, value, _style in status_items(st))["BRAIN"]
    assert "a demoted" in brain and "c key rejected" in brain
    assert next(r for r in router.status() if r["name"] == "a")["health"]["demoted_for"] > 0


def test_snapshot_carries_one_health_row_per_provider(tmp_path):
    ctx = build_context(tmp_path)
    ctx.secrets.set("groq", "gsk_abcdefghijklmnop")
    ctx.router._probe["groq"] = "key rejected"
    rows = {r["name"]: r for r in snapshot(ctx)["health"]}
    assert rows["groq"]["state"] == "key rejected"
    assert all(r["state"] in ("ok", "resting", "demoted", "key rejected", "unreachable", "no key", "off")
               for r in rows.values())


def test_selfcheck_line_names_what_is_set_up_and_what_is_wrong(tmp_path):
    ctx = build_context(tmp_path)
    line = selfcheck_line(ctx)
    assert line.startswith("Self-check:") and "models" in line and "speech" in line
    assert "no model has a key" in line  # a fresh install has none
    ctx.secrets.set("groq", "gsk_abcdefghijklmnop")
    line = selfcheck_line(ctx, probe={"groq": "key rejected"})
    assert "groq key rejected" in line


def test_tui_shows_notices():
    model = TuiModel()
    model.apply({"type": "notice", "message": "Self-check: all good"})
    assert model.notice == "Self-check: all good"


async def test_three_slow_replies_in_a_row_raise_one_alarm(tmp_path):
    p, f = build(tmp_path, stt_texts=["a", "b", "c", "d"])
    p.agent = f.agent = DelayedAgent([[(0.15, "One.")], [(0.15, "Two.")], [(0.15, "Three.")], [(0.15, "Four.")]])
    set_turn(p, notice_after_s=0, latency_alarm_ms=100, latency_alarm_turns=3)
    events = f.bus.subscribe({"notice"})
    task = asyncio.create_task(p.run())
    for word in (b"One.", b"Two.", b"Three.", b"Four."):
        f.audio.push(b"WAKE", b"SPEECH", b"END")
        await wait_until(lambda w=word: w in f.player.played)
        await wait_until(lambda: p.state == "idle")
    await finish(p, f, task)
    notices = []
    while not events.empty():
        notices.append(events.get_nowait())
    assert len(notices) == 1 and "slow" in notices[0]["message"]


async def test_alarm_off_by_zero(tmp_path):
    p, f = build(tmp_path, stt_texts=["a", "b", "c"])
    p.agent = f.agent = DelayedAgent([[(0.12, "One.")], [(0.12, "Two.")], [(0.12, "Three.")]])
    set_turn(p, notice_after_s=0, latency_alarm_ms=0)
    events = f.bus.subscribe({"notice"})
    task = asyncio.create_task(p.run())
    for word in (b"One.", b"Two.", b"Three."):
        f.audio.push(b"WAKE", b"SPEECH", b"END")
        await wait_until(lambda w=word: w in f.player.played)
        await wait_until(lambda: p.state == "idle")
    await finish(p, f, task)
    assert events.empty()


async def test_startup_checks_publish_the_line_and_the_probe_findings(tmp_path):
    from minty_core.app import startup_checks
    ctx = build_context(tmp_path)
    ctx.secrets.set("groq", "gsk_abcdefghijklmnop")

    async def probe(client=None):
        return {"groq": "key rejected"}

    ctx.router.probe = probe
    notices = ctx.bus.subscribe({"notice"})
    assert await startup_checks(ctx) == {"groq": "key rejected"}
    first, second = notices.get_nowait(), notices.get_nowait()
    assert first["message"].startswith("Self-check:") and "groq key rejected" in second["message"]


async def test_anthropic_probe_uses_the_same_base_as_its_calls(tmp_path):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={"data": []})

    router, _ = make(tmp_path, [("claude", {"kind": "anthropic", "base_url": "https://api.anthropic.com"}),
                                ("claude2", {"kind": "anthropic"})], {"claude": [OK], "claude2": [OK]})
    await router.probe(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert seen == ["https://api.anthropic.com/v1/models"] * 2  # with or without a base_url, like its calls
