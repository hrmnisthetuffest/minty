"""Clips and poster frames reach the browser through the panel: only when logged in, only by number, with byte ranges passed through so
playback starts at once, and kept by the browser for a day (they never change under their number) in a private cache only."""
import httpx
import pytest
from fastapi.testclient import TestClient

from minty_panel.auth import LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient
from minty_panel.server import create_panel_app

HDR = {"X-Requested-With": "minty"}


def build(tmp_path, core_up=True):
    passwords = PasswordStore(tmp_path)
    passwords.set("correct horse")
    seen = []

    def core(req):
        seen.append(req)
        if not core_up:
            raise httpx.ConnectError("down")
        if req.url.path == "/studio/clip/7":
            body = b"0123456789" * 10
            if req.headers.get("range") == "bytes=0-9":
                return httpx.Response(206, content=body[:10], headers={"content-type": "video/mp4", "content-range": "bytes 0-9/100",
                                                                       "accept-ranges": "bytes", "etag": '"abc"', "cache-control": "x", "set-cookie": "evil=1"})
            return httpx.Response(200, content=body, headers={"content-type": "video/mp4", "accept-ranges": "bytes", "etag": '"abc"'})
        if req.url.path == "/studio/thumb/7":
            return httpx.Response(200, content=b"\xff\xd8\xff" + b"x" * 20, headers={"content-type": "image/jpeg"})
        if req.url.path == "/studio/catalog":
            return httpx.Response(200, json={"styles": []})
        return httpx.Response(404, json={"detail": "no such clip /home/secret/path"})

    token = tmp_path / "api_token"
    token.write_text("CORE-TOKEN\n")
    client = CoreClient("http://core.test", token, httpx.AsyncClient(transport=httpx.MockTransport(core)))
    app = create_panel_app(passwords, SessionStore(), LoginThrottle(), client, allowed_hosts=("testserver",))
    c = TestClient(app)
    return c, seen


def login(c):
    assert c.post("/login", json={"password": "correct horse"}, headers=HDR).status_code == 204


def test_nothing_is_served_without_a_login(tmp_path):
    c, seen = build(tmp_path)
    assert c.get("/media/clip/7").status_code == 401 and c.get("/media/thumb/7").status_code == 401 and seen == []


def test_a_clip_is_passed_through_with_its_range_and_kept_for_a_day_privately(tmp_path):
    c, seen = build(tmp_path)
    login(c)
    whole = c.get("/media/clip/7")
    assert whole.status_code == 200 and whole.headers["content-type"] == "video/mp4" and len(whole.content) == 100
    assert whole.headers["cache-control"] == "private, max-age=86400" and whole.headers["accept-ranges"] == "bytes"
    part = c.get("/media/clip/7", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.headers["content-range"] == "bytes 0-9/100" and len(part.content) == 10
    assert part.headers["cache-control"] == "private, max-age=86400" and "set-cookie" not in part.headers
    sent = seen[-1]
    assert sent.headers["authorization"] == "Bearer CORE-TOKEN" and sent.headers["range"] == "bytes=0-9"
    assert "cookie" not in sent.headers


def test_only_the_headers_that_matter_go_to_the_core(tmp_path):
    c, seen = build(tmp_path)
    login(c)
    c.get("/media/clip/7", headers={"Range": "bytes=0-9", "If-None-Match": '"abc"', "X-Evil": "1", "Authorization": "Bearer stolen"})
    sent = seen[-1].headers
    assert sent["authorization"] == "Bearer CORE-TOKEN" and "x-evil" not in sent and sent["if-none-match"] == '"abc"'


def test_a_poster_frame_is_an_image(tmp_path):
    c, _ = build(tmp_path)
    login(c)
    r = c.get("/media/thumb/7")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["cache-control"] == "private, max-age=86400"


@pytest.mark.parametrize("path", ["/media/clip/abc", "/media/clip/7x", "/media/clip/1234567890123", "/media/other/7", "/media/clip/%2e%2e", "/media/clip/-1"])
def test_only_numbers_and_the_two_kinds_are_accepted(tmp_path, path):
    c, seen = build(tmp_path)
    login(c)
    assert c.get(path).status_code == 404 and not [r for r in seen if r.url.path.startswith("/studio")]


def test_what_the_core_says_about_a_missing_clip_is_not_repeated_to_the_browser(tmp_path):
    c, _ = build(tmp_path)
    login(c)
    r = c.get("/media/clip/8")
    assert r.status_code == 404 and "secret" not in r.text and r.headers["cache-control"] == "no-store"


def test_an_unreachable_core_is_a_502(tmp_path):
    c, _ = build(tmp_path, core_up=False)
    login(c)
    assert c.get("/media/clip/7").status_code == 502


def test_the_studio_data_goes_through_the_normal_api_door(tmp_path):
    c, _ = build(tmp_path)
    assert c.get("/api/studio/catalog").status_code == 401
    login(c)
    assert c.get("/api/studio/catalog").json() == {"styles": []}
