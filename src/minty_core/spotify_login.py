"""One-time Spotify sign-in (Authorization Code with PKCE), so Minty can read your playlists as you.

Needs only the app's Client ID. Run `minty spotify-login`; the browser opens, you click Agree, and a
refresh token is stored as the secret plugin.spotify.refresh_token. No password ever passes through Minty.
"""
import base64
import hashlib
import http.server
import secrets as pysecrets
import threading
import urllib.parse
import webbrowser

import httpx

REDIRECT = "http://127.0.0.1:8888/callback"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "playlist-read-private playlist-read-collaborative user-read-playback-state user-modify-playback-state"


def make_pkce() -> tuple[str, str]:
    verifier = pysecrets.token_urlsafe(64)[:100]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, challenge: str, state: str) -> str:
    query = urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})
    return f"{AUTH_URL}?{query}"


def wait_for_code(state: str, timeout: float = 180.0, port: int = 8888) -> str:
    """Serve exactly one request on 127.0.0.1 and return the authorization code (or raise)."""
    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            if parts.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(parts.query)
            if query.get("state", [""])[0] != state:
                result["error"] = "state mismatch"
            elif "code" in query:
                result["code"] = query["code"][0]
            else:
                result["error"] = query.get("error", ["no code returned"])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Minty is connected to Spotify.</h2><p>You can close this tab.</p>")
            done.set()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1.0
    deadline = threading.Timer(timeout, done.set)
    deadline.start()
    try:
        while not done.is_set():
            server.handle_request()
    finally:
        deadline.cancel()
        server.server_close()
    if "code" not in result:
        raise RuntimeError(result.get("error", "timed out waiting for the browser"))
    return result["code"]


def exchange_code(client: httpx.Client, client_id: str, code: str, verifier: str) -> dict:
    resp = client.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "client_id": client_id, "code_verifier": verifier})
    if resp.status_code != 200:
        raise RuntimeError(f"Spotify refused the sign-in ({resp.status_code})")
    return resp.json()


def login(secrets_store, open_browser=webbrowser.open, client: httpx.Client | None = None,
          wait=wait_for_code, say=print) -> None:
    client_id = secrets_store.get("plugin.spotify.client_id")
    if not client_id:
        raise RuntimeError("set the Client ID first: minty set-key plugin.spotify.client_id")
    verifier, challenge = make_pkce()
    state = pysecrets.token_urlsafe(16)
    url = authorize_url(client_id, challenge, state)
    say("Opening Spotify in your browser. If it doesn't open, visit:\n" + url)
    open_browser(url)
    code = wait(state)
    owns = client is None
    client = client or httpx.Client(timeout=15.0, trust_env=False)
    try:
        tokens = exchange_code(client, client_id, code, verifier)
    finally:
        if owns:
            client.close()
    if not tokens.get("refresh_token"):
        raise RuntimeError("Spotify did not return a refresh token")
    secrets_store.set("plugin.spotify.refresh_token", tokens["refresh_token"])
    say("Connected. Minty can now read your playlists.")
