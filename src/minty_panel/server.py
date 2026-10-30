import asyncio
import contextlib
import ipaddress
import logging
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from minty_panel.auth import GlobalBudget, LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient, CoreUnavailable

security = logging.getLogger("minty.security")  # who was refused and why; never a PIN, a cookie or a token
STATIC = Path(__file__).parent / "static"
COOKIE = "minty_session"
ALLOWED = {"status", "config", "secrets", "personalities", "plugins", "memory", "prompts", "restart", "restart-app", "mute", "listen", "say", "ask", "logs", "jobs", "vault", "audit", "timeline", "flags", "profile", "studio", "flows", "control"}
# The panel loads only its own files. Scripts may never be inline or built from text; styles may carry a style attribute (the progress bars).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self' ws: wss:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
LAN_ONLY_MESSAGE = "this panel answers only on your own network"


DEFAULT_HOSTS = ("127.0.0.1", "localhost", "[::1]")
CSRF_HEADER = "x-requested-with"


def _split_host(value: str) -> tuple[str, str | None]:
    value = value.strip().lower()
    if value.startswith("["):
        host, _, rest = value.partition("]")
        return host + "]", (rest[1:] or None) if rest.startswith(":") else None
    host, sep, port = value.partition(":")
    return host, (port if sep else None)


def host_allowed(value: str, allowed) -> bool:
    host, port = _split_host(value)
    for entry in allowed:
        e_host, e_port = _split_host(entry)
        if host == e_host and (e_port is None or e_port == port):
            return True
    return False


_PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                                                    "fc00::/7", "fe80::/10")]


def private_client(addr: str) -> bool:
    """True for your own network only: this computer, a home LAN address, link-local, or an IPv6 unique-local address."""
    try:
        ip = ipaddress.ip_address(str(addr).strip())
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or any(ip in n for n in _PRIVATE_NETS)


class LoginBody(BaseModel):
    password: str = Field(max_length=256)  # a PIN is 4 to 8 digits; nothing longer than a long password is hashed


def default_connect(core: CoreClient):
    import websockets

    return lambda: websockets.connect(core.ws_url(), additional_headers=core.ws_headers())


async def relay_events(ws: WebSocket, connect) -> None:
    try:
        async with connect() as upstream:
            async for message in upstream:
                await ws.send_text(message if isinstance(message, str) else message.decode())
    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await ws.close(code=1011)


class OwnFilesGzip:
    """Compress the panel's own text files (the page, scripts, styles) and nothing else. Answers that carry data are never compressed:
    a secret next to text an attacker can influence can be guessed from the size (BREACH). Fonts and pictures are already compressed."""

    SKIP = (".woff2", ".png", ".jpg", ".ico", ".webp")

    def __init__(self, app):
        self.app, self.gzip = app, GZipMiddleware(app, minimum_size=500)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        own = scope["type"] == "http" and scope.get("method") in ("GET", "HEAD") and (path == "/" or path.startswith("/static/")) \
            and not path.endswith(self.SKIP)
        await (self.gzip if own else self.app)(scope, receive, send)


def create_panel_app(passwords: PasswordStore, sessions: SessionStore, throttle: LoginThrottle,
                     core: CoreClient, *, allowed_hosts=DEFAULT_HOSTS, ws_connect=None, lan_only: bool = False,
                     login_budget: GlobalBudget | None = None) -> FastAPI:
    app = FastAPI(title="minty-panel")
    login_lock = asyncio.Lock()
    login_budget = login_budget or GlobalBudget()

    def who_is(request) -> str:
        return request.client.host if request.client else "unknown"

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if lan_only and not private_client(request.client.host if request.client else ""):
            security.warning("request refused: %s is outside your own network", who_is(request))
            response = JSONResponse({"detail": LAN_ONLY_MESSAGE}, status_code=403)
        elif not host_allowed(request.headers.get("host", ""), allowed_hosts):
            security.warning("request refused: bad host %r from %s", request.headers.get("host", "")[:80], who_is(request))
            response = JSONResponse({"detail": "bad host"}, status_code=421)
        else:
            response = await call_next(request)
        # The panel's own files hold no personal data: keep them, but ask every time whether they changed (ETag, so a reload costs a
        # 304 with no body). Anything else, and every refusal, is never stored.
        if request.url.path.startswith("/media/") and request.method == "GET" and response.status_code in (200, 206, 304):
            response.headers["Cache-Control"] = "private, max-age=86400"  # a clip never changes under its number; private: no shared cache
        own_file = request.method in ("GET", "HEAD") and response.status_code in (200, 304) and (
            request.url.path in ("/", "/sw.js") or request.url.path.startswith("/static/"))
        if "Cache-Control" not in response.headers or not request.url.path.startswith("/media/"):
            response.headers["Cache-Control"] = "no-cache" if own_file else "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def csrf_ok(request: Request) -> bool:
        return request.headers.get(CSRF_HEADER) == "minty"

    def logged_in(request: Request) -> bool:
        return sessions.valid(request.cookies.get(COOKIE, ""))

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js")
    async def service_worker():
        # at the root so its scope covers the whole panel; it still never touches /api, /login or /logout
        return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})

    @app.post("/login")
    async def login(request: Request, body: LoginBody):
        who = who_is(request)
        if not csrf_ok(request):
            security.warning("login refused: missing csrf header from %s", who)
            return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
        if throttle.blocked(who):
            wait = throttle.retry_after(who)
            security.warning("login refused: %s is locked out for another %d seconds", who, wait)
            return JSONResponse({"detail": f"too many attempts, try again in {wait} seconds"}, status_code=429,
                                headers={"Retry-After": str(wait)})
        if not passwords.is_set():
            return JSONResponse({"detail": "No PIN set. Run: minty set-pin"}, status_code=503)
        if not login_budget.take():
            security.warning("login refused: the panel is already checking as many PINs a minute as it allows (%s)", who)
            return JSONResponse({"detail": "the panel is busy checking PINs, try again in a minute"}, status_code=429,
                                headers={"Retry-After": "60"})
        throttle.record_failure(who)  # reserve the attempt before awaiting
        async with login_lock:
            ok = await asyncio.to_thread(passwords.verify, body.password)
        if not ok:
            security.warning("login failed from %s", who)
            if throttle.blocked(who):
                security.warning("login locked: %s is locked out for %d seconds", who, throttle.retry_after(who))
            return JSONResponse({"detail": "wrong PIN"}, status_code=401)
        throttle.reset(who)
        security.info("login ok from %s", who)
        response = Response(status_code=204)
        response.set_cookie(COOKIE, sessions.create(), httponly=True, samesite="strict", max_age=12 * 3600)
        return response

    @app.post("/logout")
    async def logout(request: Request):
        if not csrf_ok(request):
            return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
        sessions.destroy(request.cookies.get(COOKIE, ""))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE)
        return response

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def proxy(path: str, request: Request):
        if not logged_in(request):
            return JSONResponse({"detail": "not logged in"}, status_code=401)
        if request.method != "GET" and not csrf_ok(request):
            security.warning("api request refused: missing csrf header (%s /api/%s from %s)", request.method, path[:40], who_is(request))
            return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
        segments = path.split("/")
        if segments[0] not in ALLOWED or ".." in segments:
            return JSONResponse({"detail": "unknown endpoint"}, status_code=404)
        try:
            resp = await core.request(
                request.method, "/" + quote(path, safe="/"), params=request.query_params.multi_items(),
                content=await request.body() or None, content_type=request.headers.get("content-type"),
            )
        except (httpx.InvalidURL, ValueError):
            return JSONResponse({"detail": "unknown endpoint"}, status_code=404)
        except CoreUnavailable:
            return JSONResponse({"detail": "Minty core is not running"}, status_code=502)
        return Response(resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type"))

    @app.get("/media/{kind}/{clip_id}")
    async def media_file(kind: str, clip_id: str, request: Request):
        """A clip or its poster frame, streamed from the core with the browser's byte range, by number only."""
        if not logged_in(request):
            return JSONResponse({"detail": "not logged in"}, status_code=401)
        if kind not in ("clip", "thumb") or not (clip_id.isascii() and clip_id.isdigit() and len(clip_id) <= 9):
            return JSONResponse({"detail": "unknown file"}, status_code=404)
        forward = {k: request.headers[k] for k in ("range", "if-none-match", "if-range") if k in request.headers}
        try:
            upstream = await core.open(f"/studio/{kind}/{clip_id}", headers=forward)
        except CoreUnavailable:
            return JSONResponse({"detail": "Minty core is not running"}, status_code=502)
        if upstream.status_code not in (200, 206, 304):
            await upstream.aclose()
            return JSONResponse({"detail": "no such clip"}, status_code=404)  # the core's own words are not passed on
        keep = {k: upstream.headers[k] for k in ("content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified")
                if k in upstream.headers}
        chunks = upstream.aiter_bytes() if upstream.is_stream_consumed else upstream.aiter_raw()  # already loaded: a small or replayed answer
        return StreamingResponse(chunks, status_code=upstream.status_code, headers=keep, background=BackgroundTask(upstream.aclose))

    @app.websocket("/api/events")
    async def events(ws: WebSocket):
        # Refusals accept first and then close with an application code: a handshake refused before
        # accept() reaches browsers only as HTTP 403 / close 1006, never as 4401 or 4403. Nothing is
        # relayed before these checks pass, so the brief accept leaks no event data.
        peer = ws.client.host if ws.client else "unknown"
        if lan_only and not private_client(peer):
            security.warning("events socket refused: %s is outside your own network", peer)
            await ws.accept()
            await ws.close(code=4403)
            return
        if not sessions.valid(ws.cookies.get(COOKIE, "")):
            security.warning("events socket refused: not logged in (%s)", peer)
            await ws.accept()
            await ws.close(code=4401)
            return
        origin = ws.headers.get("origin")
        if origin is not None and not host_allowed(urlsplit(origin).netloc, allowed_hosts):
            security.warning("events socket refused: another site's page (origin %r) from %s", origin[:80], peer)
            await ws.accept()
            await ws.close(code=4403)
            return
        await ws.accept()
        await relay_events(ws, ws_connect or default_connect(core))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.add_middleware(OwnFilesGzip)
    return app
