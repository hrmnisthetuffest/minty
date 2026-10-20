from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.fakes import FakeAgent
from minty_core.api import create_app
from minty_core.context import build_context, load_or_create_token
from minty_core.llm.types import LLMError


class FakePipeline:
    def __init__(self):
        self.muted, self.state, self.said = False, "idle", []

    async def say(self, text):
        self.said.append(text)


@pytest.fixture
def ctx(tmp_path):
    c = build_context(tmp_path)
    c.agent = FakeAgent([["Hello there."]])
    return c


@pytest.fixture
def client(ctx):
    return TestClient(create_app(ctx))


@pytest.fixture
def auth(ctx):
    return {"Authorization": f"Bearer {ctx.token}"}


def test_token_is_stable_and_private(tmp_path):
    import os, stat
    first = load_or_create_token(tmp_path)
    assert load_or_create_token(tmp_path) == first and len(first) >= 32
    assert stat.S_IMODE(os.stat(tmp_path / "api_token").st_mode) == 0o600


def test_auth_required(client, auth):
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"Authorization": "Bearer nope"}).status_code == 401
    res = client.get("/status", headers=auth)
    assert res.status_code == 200 and res.json()["state"] == "offline"
    assert {p["name"] for p in res.json()["providers"]} >= {"groq", "cerebras"}


def test_invalid_config_rejected_and_unchanged(client, auth):  # RF5
    good = client.get("/config", headers=auth).json()
    bad = {**good, "personality": {**good["personality"], "humor": 99}}
    res = client.put("/config", json=bad, headers=auth)
    assert res.status_code == 422
    assert client.get("/config", headers=auth).json() == good
    ok = {**good, "personality": {**good["personality"], "humor": 7}}
    assert client.put("/config", json=ok, headers=auth).status_code == 200
    assert client.get("/config", headers=auth).json()["personality"]["humor"] == 7


def test_secrets_are_write_only_and_masked(client, auth):  # RF5
    key = "gsk_abcdefghijklmnop"
    assert client.put("/secrets/groq", json={"value": key}, headers=auth).status_code == 204
    assert client.get("/secrets", headers=auth).json() == {"groq": "gsk…mnop"}
    for path in ("/secrets", "/config", "/status", "/plugins"):
        assert key not in client.get(path, headers=auth).text
    assert client.delete("/secrets/groq", headers=auth).status_code == 204
    assert client.get("/secrets", headers=auth).json() == {}


def test_personalities_crud_and_bad_names(client, auth):  # RF5
    names = client.get("/personalities", headers=auth).json()
    assert "minty" in names
    assert client.put("/personalities/dry-wit", json={"text": "You are dry."}, headers=auth).status_code == 204
    assert client.get("/personalities/dry-wit", headers=auth).json() == {"name": "dry-wit", "text": "You are dry."}
    assert client.delete("/personalities/dry-wit", headers=auth).status_code == 204
    assert client.get("/personalities/dry-wit", headers=auth).status_code == 404
    assert client.put("/personalities/Bad Name", json={"text": "x"}, headers=auth).status_code == 400
    assert client.get("/personalities/..%2Fetc", headers=auth).status_code in (400, 404)


def test_plugin_toggle_reloads_host(client, auth):
    before = {p["name"]: p for p in client.get("/plugins", headers=auth).json()}
    assert before["clock"]["enabled"] is True and before["clock"]["tools"]
    res = client.put("/plugins/clock", json={"enabled": False}, headers=auth)
    assert res.status_code == 200
    after = {p["name"]: p for p in client.get("/plugins", headers=auth).json()}
    assert after["clock"]["enabled"] is False and after["clock"]["tools"] == []


def test_memory_list_and_delete(client, auth, ctx):
    ctx.memory.add("likes tea")
    assert client.get("/memory", headers=auth).json() == {"notes": ["likes tea"]}
    assert client.delete("/memory/5", headers=auth).status_code == 404
    assert client.delete("/memory/0", headers=auth).status_code == 204
    assert client.get("/memory", headers=auth).json() == {"notes": []}


def test_ask_returns_reply_and_maps_llm_error(client, auth, ctx):
    res = client.post("/ask", json={"text": "hi"}, headers=auth)
    assert res.json() == {"reply": "Hello there."}
    ctx.agent = FakeAgent([LLMError("down")])
    assert client.post("/ask", json={"text": "hi"}, headers=auth).status_code == 502


def test_mute_and_say_need_a_pipeline(client, auth, ctx):
    assert client.post("/mute", json={"muted": True}, headers=auth).status_code == 503
    assert client.post("/say", json={"text": "hi"}, headers=auth).status_code == 503
    ctx.pipeline = FakePipeline()
    assert client.post("/mute", json={"muted": True}, headers=auth).status_code == 204
    assert client.get("/status", headers=auth).json()["muted"] is True
    assert client.post("/say", json={"text": "testing"}, headers=auth).status_code == 204
    assert ctx.pipeline.said == ["testing"]


def test_events_websocket_requires_token(client, ctx):
    with client.websocket_connect(f"/events?token={ctx.token}") as ws:
        assert ws.receive_json() == {"type": "hello"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/events?token=bad"):
            pass


@pytest.mark.parametrize("content", ["", "  \n"])
def test_blank_token_file_is_regenerated(tmp_path, content):
    import os, stat
    (tmp_path / "api_token").write_text(content)
    token = load_or_create_token(tmp_path)
    assert len(token) >= 32
    assert (tmp_path / "api_token").read_text().strip() == token
    assert stat.S_IMODE(os.stat(tmp_path / "api_token").st_mode) == 0o600


def test_loose_token_file_is_tightened(tmp_path):
    import os, stat
    path = tmp_path / "api_token"
    path.write_text("a" * 40 + "\n")
    os.chmod(path, 0o644)
    assert load_or_create_token(tmp_path) == "a" * 40
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_create_app_rejects_empty_token(ctx):
    ctx.token = ""
    with pytest.raises(ValueError, match="empty API token"):
        create_app(ctx)


def test_logs_endpoint_filters_and_redacts(client, auth, ctx):
    import logging
    ctx.secrets.set("groq", "gsk_abcdefghijklmnop")
    rec = logging.makeLogRecord({"name": "t", "levelno": logging.WARNING, "levelname": "WARNING",
                                 "msg": "boom gsk_abcdefghijklmnop", "args": ()})
    ctx.logs.emit(rec)
    res = client.get("/logs?level=WARNING&limit=5", headers=auth)
    assert res.status_code == 200
    assert "gsk_abcdefghijklmnop" not in res.text and "boom ***" in res.text
    assert client.get("/logs").status_code == 401


@pytest.mark.parametrize("extra", [{"stop": [None, "x"]}, {"api_key": "x"}])
def test_put_config_rejects_bad_provider_extra_with_422(client, auth, extra):
    data = client.get("/config", headers=auth).json()
    data["providers"][0]["extra"] = extra
    r = client.put("/config", json=data, headers=auth)
    assert r.status_code == 422


def test_subagent_factory_builds_a_restricted_agent(ctx):
    agent = ctx.services["agent_factory"]()
    names = {s.name for s in agent._host.tool_specs()}
    assert "clock_get_time" in names
    assert not any(n.startswith(("subagents_", "agents_", "installer_")) for n in names)
    assert all(not ctx.host.get_tool(n).risky for n in names)


def test_is_private_follows_main_agent_history_and_force_flag(tmp_path):
    from minty_core.llm.types import Message

    c = build_context(tmp_path)
    is_private = c.services["is_private"]
    assert is_private() is False
    c.agent.history.append(Message("user", "hi"))
    assert is_private() is False
    c.agent.history.append(Message("tool", "secret", private=True))
    assert is_private() is True
    c.agent.history.clear()
    assert is_private() is False
    c.agent.force_private = True
    assert is_private() is True


def test_mode_round_trip_and_validation(client, auth, ctx):
    assert client.get("/mode", headers=auth).json() == {"tier": "fast", "private": False}
    res = client.put("/mode", json={"tier": "deep"}, headers=auth)
    assert res.status_code == 200 and res.json() == {"tier": "deep", "private": False}
    res = client.put("/mode", json={"private": True}, headers=auth)
    assert res.json() == {"tier": "deep", "private": True}
    assert (ctx.agent.tier, ctx.agent.force_private) == ("deep", True)
    assert client.get("/mode", headers=auth).json() == {"tier": "deep", "private": True}


@pytest.mark.parametrize("body", [{"tier": "turbo"}, {"private": "maybe"}, {"tier": 3}, {"private": [1]}])
def test_mode_rejects_bad_values_and_changes_nothing(client, auth, ctx, body):
    assert client.put("/mode", json=body, headers=auth).status_code == 422
    assert client.get("/mode", headers=auth).json() == {"tier": "fast", "private": False}


def test_mode_needs_auth(client):
    assert client.get("/mode").status_code == 401
    assert client.put("/mode", json={"tier": "deep"}).status_code == 401


def test_ask_handles_mode_commands_without_the_model(client, auth, ctx):
    res = client.post("/ask", json={"text": "Minty, private mode"}, headers=auth)
    assert res.json() == {"reply": "Private mode on. I'll only use providers that don't train on your data."}
    assert ctx.agent.force_private is True and ctx.agent.heard == []


def test_prompts_api_round_trip(tmp_path):
    from fastapi.testclient import TestClient

    from minty_core.api import create_app
    from minty_core.context import build_context

    ctx = build_context(tmp_path)
    c = TestClient(create_app(ctx), headers={"Authorization": f"Bearer {ctx.token}"})
    assert c.put("/prompts/instructions", json={"text": "Call me Boss"}).status_code == 204
    assert c.put("/prompts/instructions", json={"text": "x" * 5000}).status_code == 422
    ctx.services["prompts"].learn("Likes jazz")
    ctx.services["prompts"].add_command("movie time", "dim the lights")
    data = c.get("/prompts").json()
    assert data["instructions"].strip() == "Call me Boss"
    assert data["learned"][0]["text"] == "Likes jazz" and data["commands"][0]["phrase"] == "movie time"
    assert c.delete(f"/prompts/learned/{data['learned'][0]['id']}").status_code == 204
    assert c.delete("/prompts/commands", params={"phrase": "movie time"}).status_code == 204
    assert c.delete("/prompts/commands", params={"phrase": "movie time"}).status_code == 404
    assert "Call me Boss" in ctx.agent._system_prompt()


def test_ask_turns_are_traced(client, auth, ctx):
    from minty_core.trace import read_events
    import time as _t
    client.post("/ask", json={"text": "hi there"}, headers=auth)
    ctx.services["tracer"].flush()
    turns = [e for e in read_events(ctx.config_dir / "traces", _t.strftime("%Y-%m-%d")) if e["kind"] == "turn"]
    assert turns and turns[-1]["mode"] == "api" and turns[-1]["words"] == 2 and turns[-1]["total_ms"] >= 0


async def test_jobs_endpoints_list_and_cancel(ctx, auth):
    import asyncio
    import httpx
    from minty_core.api import create_app

    async def slow(job_ctx):
        job_ctx.progress("opening the shop")
        await asyncio.sleep(30)

    job = ctx.services["jobs"].submit("browser", "find a lamp", slow)
    await asyncio.sleep(0.02)
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rows = (await client.get("/jobs", headers=auth)).json()
        assert rows[0]["label"] == "browser job 1" and rows[0]["status"] == "running"
        assert rows[0]["progress"] == ["opening the shop"]
        assert (await client.post(f"/jobs/{job.id}/cancel", headers=auth)).json() == {"cancelled": True}
        assert (await client.post("/jobs/99/cancel", headers=auth)).json() == {"cancelled": False}


def test_status_carries_the_health_board(client, auth):
    providers = client.get("/status", headers=auth).json()["providers"]
    groq = next(p for p in providers if p["name"] == "groq")
    assert {"health", "probe", "last_error_kind"} <= set(groq) and groq["health"]["calls"] == 0
