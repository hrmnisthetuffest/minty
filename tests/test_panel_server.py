import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from minty_panel.auth import LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient
from minty_panel.server import create_panel_app


class FakeUpstream:
    def __init__(self, messages):
        self.messages = messages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self.messages:
            yield m


HDR = {"X-Requested-With": "minty"}
HOSTS = ("testserver", "127.0.0.1:7788")


def build(tmp_path, core_up=True, password="correct horse"):
    passwords = PasswordStore(tmp_path)
    if password:
        passwords.set(password)
    calls = []

    def core(req):
        calls.append(req)
        if not core_up:
            raise httpx.ConnectError("down")
        if req.url.path == "/status":
            return httpx.Response(200, json={"state": "idle"})
        if req.url.path == "/config" and req.method == "PUT":
            body = json.loads(req.content)
            return httpx.Response(422 if "bad" in body else 200, json=body)
        return httpx.Response(404, json={"detail": "nope"})

    token_path = tmp_path / "api_token"
    token_path.write_text("CORE-TOKEN\n")
    client = CoreClient("http://core.test", token_path, httpx.AsyncClient(transport=httpx.MockTransport(core)))
    app = create_panel_app(
        passwords, SessionStore(), LoginThrottle(), client,
        allowed_hosts=HOSTS,
        ws_connect=lambda: FakeUpstream(['{"type":"hello"}', '{"type":"state","state":"listening"}']),
    )
    return SimpleNamespace(client=TestClient(app), calls=calls)


def login(p, password="correct horse"):
    return p.client.post("/login", json={"password": password}, headers=HDR)


def test_index_page_is_public(tmp_path):
    res = build(tmp_path).client.get("/")
    assert res.status_code == 200 and "Minty" in res.text


def test_login_flow_cookie_flags_and_logout(tmp_path):
    p = build(tmp_path)
    assert p.client.get("/api/status").status_code == 401
    res = login(p)
    assert res.status_code == 204
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert p.client.get("/api/status").json() == {"state": "idle"}
    assert p.client.post("/logout", headers=HDR).status_code == 204
    assert p.client.get("/api/status").status_code == 401


def test_wrong_password_and_throttle(tmp_path):
    p = build(tmp_path)
    for _ in range(5):
        assert login(p, "nope nope nope").status_code == 401
    assert login(p, "nope nope nope").status_code == 429
    assert login(p).status_code == 429  # even the right password, until the window passes


def test_login_without_a_password_set_explains_how(tmp_path):
    res = login(build(tmp_path, password=None))
    assert res.status_code == 503 and "set-password" in res.json()["detail"]


def test_proxy_adds_core_token_and_not_the_browser_cookie(tmp_path):
    p = build(tmp_path)
    login(p)
    p.client.get("/api/status")
    sent = p.calls[-1]
    assert sent.headers["authorization"] == "Bearer CORE-TOKEN" and "cookie" not in sent.headers


def test_mutations_need_the_csrf_header(tmp_path):  # RF6
    p = build(tmp_path)
    login(p)
    body = {"api_port": 7777}
    assert p.client.put("/api/config", json=body).status_code == 403
    assert not any(c.method == "PUT" for c in p.calls)
    ok = p.client.put("/api/config", json=body, headers={"X-Requested-With": "minty"})
    assert ok.status_code == 200 and ok.json() == body


def test_core_errors_pass_through_and_core_down_is_502(tmp_path):
    p = build(tmp_path)
    login(p)
    bad = p.client.put("/api/config", json={"bad": 1}, headers={"X-Requested-With": "minty"})
    assert bad.status_code == 422
    down = build(tmp_path, core_up=False)
    login(down)
    res = down.client.get("/api/status")
    assert res.status_code == 502 and "not running" in res.json()["detail"]


@pytest.mark.parametrize("path", ["api_token", "events/x", "../status", "status/%2e%2e/x"])
def test_only_allowlisted_paths_are_forwarded(tmp_path, path):  # RF6
    p = build(tmp_path)
    login(p)
    before = len(p.calls)
    res = p.client.get(f"/api/{path}")
    assert res.status_code == 404 and len(p.calls) == before


def test_events_websocket_needs_session_then_relays(tmp_path):  # RF6
    p = build(tmp_path)
    # Accepted then closed with 4401, so a browser sees the real close code (a refused handshake shows as 1006).
    with p.client.websocket_connect("/api/events") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4401
    login(p)
    cookie = {"cookie": f"minty_session={p.client.cookies['minty_session']}"}
    with p.client.websocket_connect("/api/events", headers=cookie) as ws:
        assert ws.receive_json() == {"type": "hello"}
        assert ws.receive_json() == {"type": "state", "state": "listening"}


def test_login_and_logout_need_the_csrf_header(tmp_path):
    p = build(tmp_path)
    assert p.client.post("/login", json={"password": "correct horse"}).status_code == 403
    login(p)
    assert p.client.post("/logout").status_code == 403
    assert p.client.get("/api/status").status_code == 200


def test_csrf_covers_post_and_delete(tmp_path):
    p = build(tmp_path)
    login(p)
    assert p.client.post("/api/say", json={"text": "x"}).status_code == 403
    assert p.client.delete("/api/memory").status_code == 403
    assert not any(c.method in ("POST", "DELETE") for c in p.calls)


def test_old_cookie_is_dead_after_logout(tmp_path):
    p = build(tmp_path)
    login(p)
    old = {"cookie": f"minty_session={p.client.cookies['minty_session']}"}
    p.client.post("/logout", headers=HDR)
    p.client.cookies.clear()
    assert p.client.get("/api/status", headers=old).status_code == 401


def test_concurrent_wrong_logins_are_throttled(tmp_path):
    p = build(tmp_path)

    async def run():
        transport = httpx.ASGITransport(app=p.client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            rs = await asyncio.gather(*[
                c.post("/login", json={"password": "nope nope nope"}, headers=HDR) for _ in range(12)])
            right = await c.post("/login", json={"password": "correct horse"}, headers=HDR)
        return [r.status_code for r in rs], right.status_code

    codes, right = asyncio.run(run())
    assert codes.count(401) <= 5 and codes.count(429) >= 7 and right == 429


def test_bad_host_is_421(tmp_path):
    p = build(tmp_path)
    assert p.client.get("/", headers={"host": "evil.test"}).status_code == 421
    assert p.client.get("/", headers={"host": "testserver"}).status_code == 200


def test_ws_origin_checked(tmp_path):
    p = build(tmp_path)
    login(p)
    c = p.client.cookies["minty_session"]
    hdr = {"cookie": f"minty_session={c}"}
    # Accepted then closed with 4403, and no event data reaches a foreign origin before the close.
    with p.client.websocket_connect("/api/events", headers={**hdr, "origin": "http://127.0.0.1:9999"}) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4403
    for origin in ("http://127.0.0.1:7788", None):
        h = {**hdr, **({"origin": origin} if origin else {})}
        with p.client.websocket_connect("/api/events", headers=h) as ws:
            assert ws.receive_json() == {"type": "hello"}


def test_nul_in_path_is_404_and_headers_set(tmp_path):
    p = build(tmp_path)
    r = login(p)
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert p.client.get("/api/status/%00").status_code == 404
    ok = p.client.get("/api/status")
    assert ok.headers["cache-control"] == "no-store" and ok.headers["x-content-type-options"] == "nosniff"


def test_repeated_query_keys_survive(tmp_path):
    p = build(tmp_path)
    login(p)
    p.client.get("/api/status?a=1&a=2")
    assert p.calls[-1].url.params.get_list("a") == ["1", "2"]


def test_the_notes_viewer_routes_are_proxied_with_login_and_csrf_on_writes(tmp_path):
    p = build(tmp_path)
    assert p.client.get("/api/vault").status_code == 401
    login(p)
    p.client.get("/api/vault/search?q=ribbon")
    assert any(c.url.path == "/vault/search" and c.url.params.get("q") == "ribbon" for c in p.calls)
    assert p.client.post("/api/vault/notes", json={"title": "x"}).status_code == 403
    assert not any(c.method == "POST" and c.url.path == "/vault/notes" for c in p.calls)
