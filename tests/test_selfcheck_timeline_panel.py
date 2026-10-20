"""678 the timeline, 677 the phone-friendly panel behind a LAN-only gate."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.context import build_context
from minty_core.receipts import ReceiptStore
from minty_core.selfcheck.timeline import build_timeline
from minty_panel.auth import LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient
from minty_panel.server import ALLOWED, create_panel_app, private_client

STATIC = Path(__file__).parent.parent / "src" / "minty_panel" / "static"
HDR = {"X-Requested-With": "minty"}


def job(i, status, created, finished=None, goal="research fusion", result=""):
    return SimpleNamespace(id=i, kind="research", goal=goal, status=status, created=created, started=created, finished=finished,
                           result=result, label=lambda: f"research: {goal}"[:40])


def receipt(i, ts, summary, outcome="ok", risk=1):
    return SimpleNamespace(id=i, ts=ts, tool="files_write", risk=risk, summary=summary, request="", approval_id=None,
                           outcome=outcome, verified=1, detail="")


def test_jobs_and_receipts_make_one_list_newest_first():
    items = build_timeline([job(1, "done", 100, 160, result="found 4 papers"), job(2, "running", 300)],
                           [receipt(5, 200, "Saved notes.txt"), receipt(6, 50, "Opened a page", outcome="refused")])
    assert [i["id"] for i in items] == ["job:2", "receipt:5", "job:1", "receipt:6"]
    assert items[0]["kind"] == "job" and items[0]["status"] == "running" and items[1]["kind"] == "action"
    assert items[2]["detail"] == "found 4 papers" and items[3]["status"] == "refused"
    assert items[2]["ts"] == 160  # a finished job is placed when it finished


def test_the_list_is_capped_and_long_text_is_cut():
    many = [receipt(i, float(i), "x" * 500) for i in range(300)]
    items = build_timeline([], many, limit=80)
    assert len(items) == 80 and items[0]["id"] == "receipt:299" and all(len(i["title"]) <= 160 for i in items)
    assert build_timeline([], []) == []


def test_the_core_api_serves_the_timeline(tmp_path):
    ctx = build_context(tmp_path)
    store = ReceiptStore(tmp_path / "r.db")
    store.record("files_write", 1, "Saved notes.txt", request="save notes")
    ctx.services["receipts"] = store
    client = TestClient(create_app(ctx))
    auth = {"Authorization": f"Bearer {ctx.token}"}
    assert client.get("/timeline").status_code == 401
    rows = client.get("/timeline", headers=auth).json()
    assert rows and rows[0]["title"] == "Saved notes.txt" and rows[0]["kind"] == "action"


# ---- the panel ---------------------------------------------------------------------------------------------------------
def panel(tmp_path, lan_only, client_addr):
    passwords = PasswordStore(tmp_path)
    passwords.set("correct horse")
    token = tmp_path / "api_token"
    token.write_text("T\n")
    core = CoreClient("http://core.test", token, httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=[{"id": "job:1"}]))))
    app = create_panel_app(passwords, SessionStore(), LoginThrottle(), core, allowed_hosts=("testserver",), lan_only=lan_only)
    return TestClient(app, client=client_addr)


@pytest.mark.parametrize("addr,expected", [("127.0.0.1", True), ("::1", True), ("192.168.1.20", True), ("10.4.5.6", True),
                                           ("172.16.9.9", True), ("172.31.255.1", True), ("169.254.3.3", True),
                                           ("fd12:3456::1", True), ("::ffff:192.168.1.5", True),
                                           ("8.8.8.8", False), ("172.32.0.1", False), ("203.0.113.9", False),
                                           ("2001:db8::1", False), ("100.64.1.1", False), ("unknown", False), ("", False)])
def test_only_private_addresses_count_as_your_network(addr, expected):
    assert private_client(addr) is expected


def test_a_request_from_a_public_address_is_refused_before_anything_else(tmp_path):
    outside = panel(tmp_path, True, ("203.0.113.9", 50000))
    for res in (outside.get("/"), outside.get("/static/app.js"), outside.post("/login", json={"password": "correct horse"},
                                                                              headers=HDR), outside.get("/api/status")):
        assert res.status_code == 403 and res.json()["detail"] == "this panel answers only on your own network"
    inside = panel(tmp_path / "in", True, ("192.168.1.20", 50000))
    assert inside.get("/").status_code == 200
    assert inside.post("/login", json={"password": "correct horse"}, headers=HDR).status_code == 204


def test_the_gate_applies_to_the_event_stream_too(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    outside = panel(tmp_path, True, ("203.0.113.9", 50000))
    with pytest.raises(WebSocketDisconnect):
        with outside.websocket_connect("/api/events", headers={"origin": "http://testserver"}) as ws:
            ws.receive_text()


def test_the_timeline_endpoint_is_reachable_through_the_panel(tmp_path):
    assert "timeline" in ALLOWED
    p = panel(tmp_path, True, ("192.168.1.20", 50000))
    p.post("/login", json={"password": "correct horse"}, headers=HDR)
    assert p.get("/api/timeline").json() == [{"id": "job:1"}]


def test_the_pwa_files_are_valid_and_the_service_worker_never_touches_the_api(tmp_path):
    p = panel(tmp_path, True, ("192.168.1.20", 50000))
    manifest = p.get("/static/manifest.webmanifest")
    data = json.loads(manifest.text)
    assert data["display"] == "standalone" and data["start_url"] == "/" and data["name"] == "Minty"
    assert any(i["sizes"] == "any" or "512" in i["sizes"] for i in data["icons"]) and p.get("/static/icon.svg").status_code == 200
    sw = p.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and sw.headers["service-worker-allowed"] == "/"
    text = sw.text
    assert "/api" in text and "/login" in text and "/logout" in text  # named so they are explicitly skipped
    assert "respondWith" in text and re.search(r"startsWith\(\"/api", text) and "return;" in text
    html = p.get("/").text
    assert 'rel="manifest"' in html and 'name="theme-color"' in html and "viewport" in html


def test_the_phone_layout_is_in_the_stylesheet():
    css = (STATIC / "app.css").read_text()
    assert "@media (max-width" in css and "min-height:44px" in css.replace(" ", "") and "overflow-x" in css
    assert "safe-area-inset" in css and "font-size:16px" in css.replace(" ", "")
    app = (STATIC / "app.js").read_text()
    assert 'import timeline from "./pages/timeline.js"' in app and '["timeline", "Timeline", timeline]' in app
    assert "serviceWorker" in app and "isSecureContext" in app
