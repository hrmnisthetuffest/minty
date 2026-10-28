"""The panel's own files (the page, scripts, styles, the font) carry no personal data, so the browser may keep a copy and only ask
whether it changed (a 304 with no body). Everything that does carry data, and every refusal, is still never stored."""
import httpx
from fastapi.testclient import TestClient

from minty_panel.auth import LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient
from minty_panel.server import create_panel_app

HDR = {"X-Requested-With": "minty"}


def panel(tmp_path):
    passwords = PasswordStore(tmp_path)
    passwords.set("correct horse")
    token = tmp_path / "api_token"
    token.write_text("CORE\n")
    core = CoreClient("http://core.test", token, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"a": 1}))))
    return TestClient(create_panel_app(passwords, SessionStore(), LoginThrottle(), core, allowed_hosts=("testserver",)))


def test_panel_files_are_kept_but_always_revalidated(tmp_path):
    c = panel(tmp_path)
    for path in ("/", "/static/app.css", "/static/app.js", "/static/fonts/Antonio.woff2", "/sw.js"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache" and r.headers.get("etag"), path
        assert "content-security-policy" in r.headers and r.headers["x-content-type-options"] == "nosniff"


def test_an_unchanged_file_is_answered_with_no_body(tmp_path):
    c = panel(tmp_path)
    first = c.get("/static/app.css")
    again = c.get("/static/app.css", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b"" and again.headers["cache-control"] == "no-cache"
    changed = c.get("/static/app.css", headers={"If-None-Match": '"something-else"'})
    assert changed.status_code == 200 and changed.content == first.content


def test_data_login_and_refusals_are_never_stored(tmp_path):
    c = panel(tmp_path)
    assert c.post("/login", json={"password": "correct horse"}, headers=HDR).headers["cache-control"] == "no-store"
    assert c.post("/login", json={"password": "wrong wrong"}, headers=HDR).headers["cache-control"] == "no-store"
    assert c.get("/api/status").headers["cache-control"] == "no-store"
    assert c.get("/api/status", headers={"Host": "evil.example"}).headers["cache-control"] == "no-store"
    assert c.get("/static/nope.css").headers["cache-control"] == "no-store"  # a missing file is not a file
    assert c.post("/logout", headers=HDR).headers["cache-control"] == "no-store"
