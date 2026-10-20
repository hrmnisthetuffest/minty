import base64
import hashlib
import threading
import time
import urllib.parse
import urllib.request

import httpx
import pytest

from minty_core import spotify_login as sl


def test_pkce_challenge_matches_the_verifier():
    verifier, challenge = sl.make_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and 43 <= len(verifier) <= 100


def test_authorize_url_asks_for_playlist_and_playback_scopes_with_the_loopback_redirect():
    url = sl.authorize_url("cid123", "chal", "st")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert q["redirect_uri"] == ["http://127.0.0.1:8888/callback"] and q["code_challenge_method"] == ["S256"]
    assert "playlist-read-private" in q["scope"][0] and q["client_id"] == ["cid123"] and "client_secret" not in url


def test_callback_server_returns_the_code_and_rejects_a_wrong_state():
    def hit(path, delay=0.3):
        time.sleep(delay)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:18888{path}", timeout=3).read()
        except OSError:
            pass

    threading.Thread(target=hit, args=("/callback?code=THE-CODE&state=good",), daemon=True).start()
    assert sl.wait_for_code("good", timeout=5, port=18888) == "THE-CODE"
    threading.Thread(target=hit, args=("/callback?code=EVIL&state=bad",), daemon=True).start()
    with pytest.raises(RuntimeError, match="state mismatch"):
        sl.wait_for_code("good", timeout=5, port=18888)


def test_login_stores_only_the_refresh_token(tmp_path):
    from minty_core.keystore import SecretsStore

    store = SecretsStore(tmp_path)
    store.set("plugin.spotify.client_id", "cid")
    seen = {}

    def handler(req):
        seen["body"] = urllib.parse.parse_qs(req.content.decode())
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "REFRESH", "expires_in": 3600})

    opened = []
    sl.login(store, open_browser=opened.append, client=httpx.Client(transport=httpx.MockTransport(handler)),
             wait=lambda state: "CODE", say=lambda *_: None)
    assert store.get("plugin.spotify.refresh_token") == "REFRESH"
    assert seen["body"]["code"] == ["CODE"] and seen["body"]["client_id"] == ["cid"]
    assert "code_verifier" in seen["body"] and "client_secret" not in seen["body"]
    assert opened and opened[0].startswith("https://accounts.spotify.com/authorize")


def test_login_without_a_client_id_explains_what_to_do(tmp_path):
    from minty_core.keystore import SecretsStore

    with pytest.raises(RuntimeError, match="Client ID"):
        sl.login(SecretsStore(tmp_path), open_browser=lambda u: None, say=lambda *_: None)
