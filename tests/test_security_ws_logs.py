"""The control API's token must never be part of a URL, and no log line may carry one.

uvicorn writes `WebSocket /events?token=...` to its error logger at INFO, and Minty's log file and a systemd journal keep
what it writes. The panel therefore sends the token in a header, the core refuses it in the query string, and a filter on
the two uvicorn loggers hides any secret-looking query value that still shows up (a script or an old panel).
"""
import asyncio
import logging
import socket
import stat

import pytest
import uvicorn
import websockets
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from minty_core.api import create_app
from minty_core.context import build_context
from minty_core.logredact import PrivateRotatingFileHandler, QuerySecretFilter, install_uvicorn_redaction
from minty_panel.core_client import CoreClient
from minty_panel.server import default_connect
from tests.fakes import FakeAgent

TOKEN = "tok-" + "A1b2C3d4" * 5


@pytest.fixture
def ctx(tmp_path):
    c = build_context(tmp_path)
    c.agent = FakeAgent([["Hello there."]])
    return c


def test_the_events_socket_takes_the_token_in_a_header_only(ctx):
    client = TestClient(create_app(ctx))
    with client.websocket_connect("/events", headers={"Authorization": f"Bearer {ctx.token}"}) as ws:
        assert ws.receive_json() == {"type": "hello"}
    for url, headers in ((f"/events?token={ctx.token}", {}), ("/events", {}), ("/events", {"Authorization": "Bearer wrong"})):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(url, headers=headers):
                pass


def test_the_panel_connects_to_the_core_without_a_token_in_the_address(tmp_path):
    (tmp_path / "api_token").write_text(TOKEN + "\n")
    core = CoreClient("http://127.0.0.1:7777", tmp_path / "api_token")
    assert core.ws_url() == "ws://127.0.0.1:7777/events" and "token" not in core.ws_url().lower()
    assert core.ws_headers() == {"Authorization": f"Bearer {TOKEN}"}


def record(msg, *args, name="uvicorn.error"):
    return logging.LogRecord(name, logging.INFO, __file__, 1, msg, args, None)


@pytest.mark.parametrize("query", ["token=SECRETVALUE123", "x=1&token=SECRETVALUE123&y=2", "api_key=SECRETVALUE123",
                                   "access_token=SECRETVALUE123", "key=SECRETVALUE123", "password=SECRETVALUE123"])
def test_secret_looking_query_values_are_hidden_in_a_log_line(query):
    rec = record('%s - "WebSocket %s" [accepted]', "127.0.0.1:5000", f"/events?{query}")
    assert QuerySecretFilter().filter(rec) is True
    text = rec.getMessage()
    assert "SECRETVALUE123" not in text and "/events?" in text and "***" in text


def test_other_query_values_and_plain_lines_are_left_alone():
    rec = record('%s - "GET %s HTTP/1.1" 200', "127.0.0.1:5000", "/api/logs?limit=50&level=INFO")
    QuerySecretFilter().filter(rec)
    assert rec.getMessage() == '127.0.0.1:5000 - "GET /api/logs?limit=50&level=INFO HTTP/1.1" 200'


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_a_real_socket_never_puts_the_token_in_a_log_line(ctx, tmp_path):
    seen: list[str] = []

    class Collect(logging.Handler):
        def emit(self, rec):
            seen.append(rec.getMessage())

    handler = Collect()
    filters = install_uvicorn_redaction()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=port, log_level="info", lifespan="off"))
    loggers = [logging.getLogger(n) for n in ("uvicorn.error", "uvicorn.access")]
    for lg in loggers:  # after Config(): building it re-configures these loggers and drops handlers added earlier
        lg.addHandler(handler)
        lg.setLevel(logging.INFO)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        legacy = f"ws://127.0.0.1:{port}/events?token={ctx.token}"  # what an old panel or a script might still send
        with pytest.raises(Exception):
            async with websockets.connect(legacy) as ws:
                await ws.recv()
        (tmp_path / "api_token").write_text(ctx.token + "\n")
        core = CoreClient(f"http://127.0.0.1:{port}", tmp_path / "api_token")
        async with websockets.connect(core.ws_url(), additional_headers=core.ws_headers()) as ws:
            assert '"hello"' in await ws.recv()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        for lg in loggers:
            lg.removeHandler(handler)
        for lg, flt in filters:
            lg.removeFilter(flt)
    assert any("/events" in line for line in seen), seen  # the lines exist, so the check below means something
    assert not any(ctx.token in line for line in seen), "the API token reached a log line"


def test_the_default_panel_connector_sends_the_header(tmp_path, monkeypatch):
    (tmp_path / "api_token").write_text(TOKEN + "\n")
    core = CoreClient("http://127.0.0.1:7777", tmp_path / "api_token")
    calls = []
    monkeypatch.setattr(websockets, "connect", lambda *a, **k: calls.append((a, k)) or "conn")
    assert default_connect(core)() == "conn"
    assert calls == [(("ws://127.0.0.1:7777/events",), {"additional_headers": {"Authorization": f"Bearer {TOKEN}"}})]


def test_the_log_file_is_private_from_the_first_line_and_after_a_rollover(tmp_path):
    path = tmp_path / "minty.log"
    handler = PrivateRotatingFileHandler(path, maxBytes=200, backupCount=1)
    handler.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("test.private.log")
    lg.addHandler(handler)
    lg.setLevel(logging.INFO)
    try:
        for i in range(20):
            lg.info("line %d %s", i, "x" * 40)
    finally:
        lg.removeHandler(handler)
        handler.close()
    assert path.exists() and (tmp_path / "minty.log.1").exists()
    for p in (path, tmp_path / "minty.log.1"):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p


async def test_a_closed_events_socket_is_forgotten_at_once_and_does_not_hold_up_shutdown(ctx, tmp_path):
    port = free_port()
    (tmp_path / "api_token").write_text(ctx.token + "\n")
    core = CoreClient(f"http://127.0.0.1:{port}", tmp_path / "api_token")
    server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        base = len(ctx.bus._subs)  # the overlay feed and friends are subscribers already
        async with websockets.connect(core.ws_url(), additional_headers=core.ws_headers()) as ws:
            await ws.recv()
            assert len(ctx.bus._subs) == base + 1
        for _ in range(40):  # no event is published here: only the disconnect can tell the server
            if len(ctx.bus._subs) == base:
                break
            await asyncio.sleep(0.05)
        assert len(ctx.bus._subs) == base, "a closed socket kept its place on the event bus"
        async with websockets.connect(core.ws_url(), additional_headers=core.ws_headers()) as ws:  # still open at shutdown
            await ws.recv()
            server.should_exit = True
            await asyncio.wait_for(task, 5)
    finally:
        server.should_exit = True
        if not task.done():
            task.cancel()
