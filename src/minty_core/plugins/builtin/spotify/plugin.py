"""Spotify by voice.

Control goes through the desktop app (MPRIS via playerctl), so it needs no Premium API access.
Finding a song by name uses Spotify's public search with a free developer app (client id + secret):
    minty set-key plugin.spotify.client_id
    minty set-key plugin.spotify.client_secret
Without them pause/resume/next/previous/volume/now-playing still work.
"""
import asyncio
import base64
import difflib
import logging
import random
import re
import shutil
import socket
import time

import httpx

from minty_core.plugins.host import Tool

log = logging.getLogger(__name__)

KINDS = ("track", "artist", "album", "playlist")
_PLAYER = re.compile(r"[A-Za-z0-9_.\-]{1,40}")
_URI = re.compile(r"spotify:(track|artist|album|playlist):[A-Za-z0-9]{10,40}")
TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"
PLAYLIST_URL = "https://api.spotify.com/v1/playlists/{id}/items"  # /tracks now answers 403
PLAYER_URL = "https://api.spotify.com/v1/me/player"
_PLAYLIST_ID = re.compile(r"(?:playlist[/:])?([A-Za-z0-9]{16,40})(?:[?/#].*)?$")
PLAYLIST_TTL = 600.0
MAX_PLAYLIST_TRACKS = 1000
_WORDS = re.compile(r"[a-z0-9']+")
_NOISE = {"the", "a", "an", "by", "from", "song", "track", "play", "me", "some", "of", "and", "feat", "ft", "something", "anything",
          "songs", "music", "stuff", "spotify", "playing", "start", "put", "on", "any", "one", "tracks", "please", "my", "your", "playlist"}


def parse_playlist_id(value: str) -> str | None:
    """Accepts an open.spotify.com link (with ?si=...), a spotify:playlist: URI, or a bare id."""
    text = str(value or "").strip()
    m = _PLAYLIST_ID.search(text.split("?")[0] if text.startswith("http") else text)
    return m.group(1) if m else None


def _tokens(text: str) -> list[str]:
    return [w for w in _WORDS.findall(text.lower()) if w not in _NOISE]


def pick_from_playlist(query: str, tracks: list[dict]) -> tuple[dict | None, str]:
    """Best track in the user's playlist for a spoken request, with how it matched: 'track', 'artist' or ''."""
    want = _tokens(query)
    if not want:
        return None, ""
    scored = []
    for t in tracks:
        title, artists = _tokens(t["name"]), _tokens(" ".join(t["artists"]))
        hay = set(title) | set(artists)
        if all(any(w == h or difflib.SequenceMatcher(None, w, h).ratio() >= 0.85 for h in hay) for w in want):
            overlap = sum(1 for w in want if w in title)
            scored.append((overlap, -len(title), t))
    if scored:
        best = max(s[0] for s in scored)
        matches = [t for o, _, t in scored if o == best]
        by_artist_only = best == 0
        return random.choice(matches), "artist" if by_artist_only else "track"
    whole = " ".join(want)
    close = [(difflib.SequenceMatcher(None, whole, " ".join(_tokens(t["name"]))).ratio(), t) for t in tracks]
    close = [c for c in close if c[0] >= 0.78]
    return (max(close, key=lambda c: c[0])[1], "track") if close else (None, "")


class SpotifyError(Exception):
    pass


async def run_playerctl(*args: str) -> tuple[int, str]:
    """playerctl via exec (no shell). Returns (exit code, stdout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "playerctl", *args, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        from minty_core.system.osinfo import detect, hint_for

        raise SpotifyError(hint_for(detect(), "playerctl")) from None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
    except asyncio.TimeoutError:
        proc.kill()
        raise SpotifyError("Spotify did not answer") from None
    return proc.returncode, out.decode(errors="replace").strip()


async def launch_spotify() -> bool:
    """Start the Spotify desktop program itself, detached. Never via xdg-open: on some desktops an unknown
    spotify: link makes the software centre offer to download and install an app."""
    for cmd in (["spotify"], ["flatpak", "run", "com.spotify.Client"]):
        if shutil.which(cmd[0]) is None:
            continue
        if cmd[0] == "flatpak":
            check = await asyncio.create_subprocess_exec(
                "flatpak", "info", "com.spotify.Client", stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            if await check.wait() != 0:
                continue
        await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        return True
    return False


def register(ctx):
    player = str((ctx.settings or {}).get("player") or "spotify")
    if not _PLAYER.fullmatch(player):
        raise ValueError("invalid player name")
    playlist_id = parse_playlist_id((ctx.settings or {}).get("playlist") or "")
    cache: dict = {"tracks": [], "at": 0.0, "name": None}
    token = {"value": "", "expires": 0.0}
    http: dict = {}

    def client() -> httpx.AsyncClient:
        if "c" not in http:
            http["c"] = httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False)
        return http["c"]

    async def ctl(*args: str) -> tuple[int, str]:
        code, out = await run_playerctl("-p", player, *args)
        return code, out

    async def need_player() -> None:
        code, _ = await ctl("status")
        if code != 0:
            raise SpotifyError("Spotify is not running. Open Spotify first, or ask me to play something and I'll "
                               "start it.")

    async def access_token() -> str:
        if token["value"] and time.time() < token["expires"] - 30:
            return token["value"]
        cid, secret = ctx.secret("client_id"), ctx.secret("client_secret")
        if not cid or not secret:
            raise SpotifyError("To search by name I need a Spotify developer app. Make one free at "
                               "developer.spotify.com, then run: minty set-key plugin.spotify.client_id "
                               "and plugin.spotify.client_secret.")
        basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        try:
            resp = await client().post(TOKEN_URL, data={"grant_type": "client_credentials"},
                                       headers={"Authorization": f"Basic {basic}"})
        except httpx.HTTPError:
            raise SpotifyError("could not reach Spotify") from None
        if resp.status_code != 200:
            raise SpotifyError("Spotify rejected the developer app credentials")
        data = resp.json()
        token.update(value=data["access_token"], expires=time.time() + float(data.get("expires_in", 3600)))
        return token["value"]

    user_token = {"value": "", "expires": 0.0}

    async def user_access_token() -> str | None:
        """A token acting as the signed-in user (from `minty spotify-login`), or None if not signed in."""
        refresh, cid = ctx.secret("refresh_token"), ctx.secret("client_id")
        if not refresh or not cid:
            return None
        if user_token["value"] and time.time() < user_token["expires"] - 30:
            return user_token["value"]
        try:
            resp = await client().post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": refresh,
                                                        "client_id": cid})
        except httpx.HTTPError:
            raise SpotifyError("could not reach Spotify") from None
        if resp.status_code != 200:
            raise SpotifyError("Spotify sign-in expired; run: minty spotify-login")
        data = resp.json()
        user_token.update(value=data["access_token"], expires=time.time() + float(data.get("expires_in", 3600)))
        return user_token["value"]

    async def search(query: str, kind: str) -> tuple[str, str]:
        bearer = await access_token()
        try:
            resp = await client().get(SEARCH_URL, params={"q": query[:100], "type": kind, "limit": 1},
                                      headers={"Authorization": f"Bearer {bearer}"})
        except httpx.HTTPError:
            raise SpotifyError("could not reach Spotify") from None
        if resp.status_code == 401:
            token["value"] = ""
            raise SpotifyError("Spotify rejected the search; try again")
        if resp.status_code != 200:
            raise SpotifyError(f"Spotify search failed ({resp.status_code})")
        items = resp.json().get(f"{kind}s", {}).get("items", [])
        items = [i for i in items if i]
        if not items:
            raise SpotifyError(f"I couldn't find a {kind} called {query!r} on Spotify")
        item = items[0]
        uri = str(item.get("uri", ""))
        if not _URI.fullmatch(uri):
            raise SpotifyError("Spotify returned something I can't play")
        by = ", ".join(a["name"] for a in item.get("artists", [])[:2]) or item.get("owner", {}).get("display_name", "")
        return uri, f"{item.get('name', query)}" + (f" by {by}" if by else "")

    async def playlist_name() -> str:
        """The playlist's own title (e.g. 'i love music'), so asking for it by name plays it."""
        if cache["name"] is None:
            cache["name"] = ""
            bearer = await user_access_token()
            if bearer:
                try:
                    resp = await client().get(f"https://api.spotify.com/v1/playlists/{playlist_id}",
                                              params={"fields": "name"}, headers={"Authorization": f"Bearer {bearer}"})
                    if resp.status_code == 200:
                        cache["name"] = str(resp.json().get("name") or "")
                except httpx.HTTPError:
                    cache["name"] = None  # try again next time
        return cache["name"] or ""

    async def playlist_tracks() -> list[dict]:
        if cache["tracks"] and time.time() - cache["at"] < PLAYLIST_TTL:
            return cache["tracks"]
        bearer = await user_access_token()
        if bearer is None:
            raise SpotifyError("to read your playlist I need you to sign in once: minty spotify-login")
        tracks, url, params = [], PLAYLIST_URL.format(id=playlist_id), {
            "limit": 100, "fields": "next,items(item(name,uri,artists(name)),track(name,uri,artists(name)))"}
        while url and len(tracks) < MAX_PLAYLIST_TRACKS:
            try:
                resp = await client().get(url, params=params, headers={"Authorization": f"Bearer {bearer}"})
            except httpx.HTTPError:
                raise SpotifyError("could not reach Spotify") from None
            if resp.status_code == 401:
                user_token["value"] = ""
            if resp.status_code != 200:
                raise SpotifyError(f"could not read your playlist ({resp.status_code})")
            data = resp.json()
            for item in data.get("items", []):
                track = (item or {}).get("item") or (item or {}).get("track") or {}
                if _URI.fullmatch(str(track.get("uri", ""))):
                    tracks.append({"name": track.get("name", ""), "uri": track["uri"],
                                   "artists": [a.get("name", "") for a in track.get("artists", [])]})
            url, params = data.get("next"), None
            if url and not str(url).startswith("https://api.spotify.com/"):
                break
        cache.update(tracks=tracks, at=time.time())
        return tracks

    async def local_device(bearer: str) -> str | None:
        """This computer's Spotify device id (Spotify forgets which device is 'active' after a while idle)."""
        try:
            resp = await client().get(f"{PLAYER_URL}/devices", headers={"Authorization": f"Bearer {bearer}"})
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        devices = [d for d in resp.json().get("devices", []) if d.get("id") and not d.get("is_restricted")]
        host = socket.gethostname().lower()
        for pick in (lambda d: str(d.get("name", "")).lower() == host,
                     lambda d: d.get("type") == "Computer" and d.get("is_active"),
                     lambda d: d.get("type") == "Computer",
                     lambda d: d.get("is_active")):
            for d in devices:
                if pick(d):
                    return d["id"]
        return None

    async def running() -> bool:
        code, _ = await ctl("status")
        return code == 0

    async def wake_app() -> bool:
        """Make sure the desktop app is running; start the program (not a link) if it is not."""
        if await running():
            return True
        if not await launch_spotify():
            return False
        for _ in range(20):  # up to ~10 s for it to start and register with Spotify
            await asyncio.sleep(0.5)
            if await running():
                await asyncio.sleep(1.5)
                return True
        return False

    async def web_play(context_uri: str | None, track_uri: str | None = None, uris: list[str] | None = None) -> bool:
        """Start playback with Spotify's own API so the song plays *inside* the given playlist, and what comes next
        is the playlist's next song. Needs the sign-in; returns False when it cannot (then the caller falls back)."""
        bearer = await user_access_token()
        if bearer is None:
            return False
        body: dict = {"uris": uris} if uris else {"context_uri": context_uri}
        if track_uri and not uris:
            body["offset"] = {"uri": track_uri}
        headers = {"Authorization": f"Bearer {bearer}"}
        params: dict = {}
        for attempt in range(3):
            try:
                resp = await client().put(f"{PLAYER_URL}/play", json=body, headers=headers, params=params or None)
            except httpx.HTTPError:
                return False
            if resp.status_code in (200, 202, 204):
                return True
            if resp.status_code == 404 and attempt < 2:  # "no active device": aim at this computer's player
                if attempt == 1 and not await wake_app():
                    return False
                device = await local_device(bearer)
                if device:
                    params = {"device_id": device}
                continue
            log.info("Spotify refused web playback (%s)", resp.status_code)
            return False
        return False

    async def start(uri: str, title: str, context: str | None = None) -> str:
        if context and await web_play(context, uri if uri != context else None):
            return f"Playing {title}."
        if not context and uri.startswith("spotify:track:") and await web_play(None, uris=[uri]):
            return f"Playing {title}."
        started = False
        if not await running():
            if not await wake_app():
                raise SpotifyError("Spotify isn't installed or wouldn't start")
            started = True
        code, _ = await ctl("open", uri)
        if code != 0:
            raise SpotifyError("Spotify did not accept that")
        return f"{'Opening Spotify and playing' if started else 'Playing'} {title}."

    def guarded(fn):
        async def run(args):
            try:
                return await fn(args)
            except SpotifyError as e:
                return f"Error: {e}"
        return run

    async def play(args):
        query = " ".join(str(args.get("query") or "").split())
        kind = str(args.get("kind") or "track").lower()
        if kind not in KINDS:
            kind = "track"
        log.info("spotify play: query=%r kind=%s playlist=%s", query, kind, bool(playlist_id))
        generic = not _tokens(query)  # "play music", "play some songs", "play spotify" name nothing in particular
        if not generic and playlist_id:
            try:
                own = " ".join(_WORDS.findall((await playlist_name()).lower()))
            except SpotifyError:
                own = ""
            asked = " ".join(_WORDS.findall(query.lower()))
            generic = bool(own) and (asked == own or asked in (f"{own} playlist", f"my {own}", f"my {own} playlist"))
        if generic:
            if playlist_id:  # the user's own playlist, shuffled
                context = f"spotify:playlist:{playlist_id}"
                reply = await start(context, "your playlist", context)
                await ctl("shuffle", "On")
                return reply
            code, _ = await ctl("play")
            if code != 0:
                raise SpotifyError("Spotify is not running")
            return "Resumed."
        not_in_playlist = ""
        if playlist_id:  # always the user's playlist first, whatever 'kind' the model asked for
            try:
                found, how = pick_from_playlist(query, await playlist_tracks())
            except SpotifyError as e:  # cannot read it: search instead, and don't claim the song is missing
                log.info("playlist lookup failed, searching Spotify instead: %s", e)
                needs_login = "spotify-login" in str(e)
                not_in_playlist = ("I couldn't read your playlist" + (" (run minty spotify-login once)"
                                                                       if needs_login else "") + ", so ")
            else:
                if found is not None:
                    by = ", ".join(found["artists"][:2])
                    return await start(found["uri"], f"{found['name']} by {by} from your playlist",
                                       f"spotify:playlist:{playlist_id}")
                not_in_playlist = "That isn't in your playlist, so "
        # not in the playlist: one specific song from all of Spotify, never a whole unrelated playlist
        uri, title = await search(query, "track" if playlist_id else kind)
        reply = await start(uri, title)
        return (not_in_playlist + reply[0].lower() + reply[1:]) if not_in_playlist else reply

    async def simple(command: str, said: str, args=None):
        await need_player()
        code, _ = await ctl(command, *(args or []))
        if code != 0:
            raise SpotifyError("Spotify did not accept that")
        return said

    async def pause(a):
        return await simple("pause", "Paused.")

    async def resume(a):
        return await simple("play", "Playing.")

    async def next_track(a):
        return await simple("next", "Skipped.")

    async def previous(a):
        return await simple("previous", "Going back.")

    async def volume(a):
        try:
            level = max(0, min(100, int(a.get("percent"))))
        except (TypeError, ValueError):
            raise SpotifyError("give a volume from 0 to 100") from None
        await simple("volume", "", [f"{level / 100:.2f}"])
        return f"Spotify volume {level}%."

    async def shuffle(a):
        return await simple("shuffle", "Shuffle on." if a.get("on") is not False else "Shuffle off.",
                            ["On" if a.get("on") is not False else "Off"])

    async def now_playing(a):
        await need_player()
        code, out = await ctl("metadata", "--format", "{{status}}|{{title}}|{{artist}}|{{album}}")
        if code != 0 or "|" not in out:
            raise SpotifyError("nothing is playing")
        status, title, artist, album = (out.split("|") + ["", "", ""])[:4]
        if not title:
            return f"Spotify is {status.lower() or 'idle'}."
        return f"{status}: {title} by {artist}" + (f" from {album}" if album else "") + "."

    q = {"type": "object", "properties": {
        "query": {"type": "string", "description": "what to play, e.g. 'Blinding Lights The Weeknd'"},
        "kind": {"type": "string", "description": "track (default), artist, album or playlist"}}}
    none = {"type": "object", "properties": {}}
    return [
        Tool("play", "Play music on Spotify. query: exactly what the user asked to hear (song and/or artist); their "
                     "own playlists are searched first, so never swap in a playlist name. Empty query for 'play some "
                     "music'.", q,
             guarded(play)),
        Tool("pause", "Pause Spotify.", none, guarded(pause)),
        Tool("resume", "Resume Spotify.", none, guarded(resume)),
        Tool("next", "Skip to the next track.", none, guarded(next_track)),
        Tool("previous", "Go back to the previous track.", none, guarded(previous)),
        Tool("volume", "Set Spotify's volume, 0 to 100 percent.",
             {"type": "object", "properties": {"percent": {"type": "number"}}, "required": ["percent"]},
             guarded(volume)),
        Tool("shuffle", "Turn shuffle on or off.", {"type": "object", "properties": {"on": {"type": "boolean"}}},
             guarded(shuffle)),
        Tool("now_playing", "What is playing on Spotify right now.", none, guarded(now_playing)),
    ]
