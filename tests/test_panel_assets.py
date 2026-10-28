"""What the panel sends the first time: its font is woff2 (half the bytes) and its own text files are compressed. Data answers are not."""
import httpx
from fastapi.testclient import TestClient

from minty_panel.auth import LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient
from minty_panel.server import STATIC, create_panel_app

HDR = {"X-Requested-With": "minty"}


def panel(tmp_path):
    passwords = PasswordStore(tmp_path)
    passwords.set("correct horse")
    token = tmp_path / "api_token"
    token.write_text("CORE\n")
    big = {"rows": ["x" * 40 for _ in range(200)]}
    core = CoreClient("http://core.test", token, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=big))))
    return TestClient(create_panel_app(passwords, SessionStore(), LoginThrottle(), core, allowed_hosts=("testserver",)))


def test_the_font_is_woff2_and_everything_that_names_it_agrees():
    assert (STATIC / "fonts" / "Antonio.woff2").stat().st_size < 45_000
    assert not (STATIC / "fonts" / "Antonio.ttf").exists()
    assert "Antonio.woff2" in (STATIC / "app.css").read_text() and "Antonio.ttf" not in (STATIC / "app.css").read_text()
    sw = (STATIC / "sw.js").read_text()
    assert "Antonio.woff2" in sw and "Antonio.ttf" not in sw


def test_the_font_is_served_with_its_type_and_not_compressed_again(tmp_path):
    r = panel(tmp_path).get("/static/fonts/Antonio.woff2", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("font/woff2") and "content-encoding" not in r.headers


def test_text_files_are_compressed_when_the_browser_accepts_it(tmp_path):
    c = panel(tmp_path)
    plain = (STATIC / "app.css").read_bytes()
    r = c.get("/static/app.css", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and "accept-encoding" in r.headers["vary"].lower()
    assert r.num_bytes_downloaded < len(plain) * 0.4 and r.headers["cache-control"] == "no-cache"
    assert "content-security-policy" in r.headers
    assert r.content == plain  # the client undid the compression: it is the same file
    assert "content-encoding" not in c.get("/static/app.css", headers={"Accept-Encoding": "identity"}).headers


def test_data_answers_are_never_compressed(tmp_path):
    c = panel(tmp_path)
    c.post("/login", json={"password": "correct horse"}, headers=HDR)
    r = c.get("/api/status", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and len(r.content) > 5000 and "content-encoding" not in r.headers  # a secret next to a guess can leak by size
