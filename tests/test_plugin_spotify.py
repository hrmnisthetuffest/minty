import httpx

import minty_core.httpclient  # noqa: F401  (import before httpx.AsyncClient is patched)
import pytest

from tests.plugin_helpers import make_host

REAL = httpx.AsyncClient
URI = "spotify:track:0VjIjW4GlUZAMYd2vXMi3b"


class Ctl:
    def __init__(self, running=True, metadata="Playing|Blinding Lights|The Weeknd|After Hours"):
        self.calls, self.running, self.metadata = [], running, metadata

    async def __call__(self, *args):
        self.calls.append(args)
        if not self.running:
            return 1, ""
        if args[2:3] == ("metadata",):
            return 0, self.metadata
        return 0, "Playing"


def host_for(tmp_path, monkeypatch, ctl=None, http=None, keys=True, signed_in=True, **settings):
    ctl = ctl or Ctl()
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.host == "accounts.spotify.com" and not getattr(http, "accounts", False):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if http is None and req.url.path.startswith("/v1/me/player/play"):
            return httpx.Response(403, json={"error": {"reason": "PREMIUM_REQUIRED"}})  # desktop-link path
        return http(req) if http else httpx.Response(200, json={"tracks": {"items": [
            {"uri": URI, "name": "Blinding Lights", "artists": [{"name": "The Weeknd"}]}]}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL(transport=httpx.MockTransport(handler), **kw))
    host = make_host(tmp_path, plugins={"spotify": {"enabled": True, **settings}},
                     secrets=({"plugin.spotify.client_id": "cid", "plugin.spotify.client_secret": "sec",
                               **({"plugin.spotify.refresh_token": "r-token"} if signed_in else {})}
                              if keys else {}))
    monkeypatch.setitem(host.get_tool("spotify_play").func.__globals__, "run_playerctl", ctl)
    opened = []

    async def fake_launch():
        opened.append("launched")
        ctl.running = True  # the app comes up
        return True

    g = host.get_tool("spotify_play").func.__globals__
    monkeypatch.setitem(g, "launch_spotify", fake_launch)
    return host, ctl, seen, opened


async def test_play_searches_then_opens_the_uri_in_the_app(tmp_path, monkeypatch):
    host, ctl, seen, _ = host_for(tmp_path, monkeypatch)
    out = await host.call("spotify_play", {"query": "blinding lights the weeknd"})
    assert out == "Playing Blinding Lights by The Weeknd."
    assert ("-p", "spotify", "open", URI) in ctl.calls
    search = [r for r in seen if r.url.host == "api.spotify.com"][0]
    assert search.url.params["q"] == "blinding lights the weeknd" and search.url.params["type"] == "track"
    assert search.headers["authorization"] == "Bearer tok"


async def test_play_starts_the_spotify_program_when_it_is_not_running(tmp_path, monkeypatch):
    host, ctl, _, opened = host_for(tmp_path, monkeypatch, ctl=Ctl(running=False))
    out = await host.call("spotify_play", {"query": "blinding lights"})
    assert out.startswith("Opening Spotify") and opened == ["launched"]
    assert ("-p", "spotify", "open", URI) in ctl.calls


async def test_without_a_developer_app_search_explains_and_controls_still_work(tmp_path, monkeypatch):
    host, ctl, _, _ = host_for(tmp_path, monkeypatch, keys=False)
    out = await host.call("spotify_play", {"query": "blinding lights"})
    assert out.startswith("Error: To search by name I need a Spotify developer app")
    assert await host.call("spotify_pause", {}) == "Paused."
    assert ("-p", "spotify", "pause") in ctl.calls


async def test_controls_and_now_playing(tmp_path, monkeypatch):
    host, ctl, _, _ = host_for(tmp_path, monkeypatch)
    assert await host.call("spotify_next", {}) == "Skipped."
    assert await host.call("spotify_volume", {"percent": 150}) == "Spotify volume 100%."
    assert ("-p", "spotify", "volume", "1.00") in ctl.calls
    assert (await host.call("spotify_volume", {"percent": "loud"})).startswith("Error")
    assert await host.call("spotify_now_playing", {}) == "Playing: Blinding Lights by The Weeknd from After Hours."


async def test_nothing_found_and_bad_uris_are_refused(tmp_path, monkeypatch):
    host, ctl, _, _ = host_for(tmp_path, monkeypatch,
                               http=lambda r: httpx.Response(200, json={"tracks": {"items": []}}))
    assert "couldn't find" in await host.call("spotify_play", {"query": "zzzzqqq"})
    host2, ctl2, _, _ = host_for(tmp_path / "b", monkeypatch, http=lambda r: httpx.Response(
        200, json={"tracks": {"items": [{"uri": "https://evil.example/x; rm", "name": "x"}]}}))
    assert "can't play" in await host2.call("spotify_play", {"query": "x"})
    assert not any(a[2:3] == ("open",) for a in ctl2.calls)


async def test_empty_play_just_resumes_and_secrets_never_leak(tmp_path, monkeypatch):
    host, ctl, _, _ = host_for(tmp_path, monkeypatch)
    assert await host.call("spotify_play", {}) == "Resumed."
    host2, _, _, _ = host_for(tmp_path / "c", monkeypatch, http=lambda r: httpx.Response(500))
    out = await host2.call("spotify_play", {"query": "x"})
    assert "sec" not in out.replace("search", "") and "cid" not in out


PLAYLIST = "0tXYOzbYXsG9sIDjyxEQt6"
LINK = f"https://open.spotify.com/playlist/{PLAYLIST}?si=ef33f2fd308049c7"
LIST = {"items": [
    {"track": {"name": "Shabang", "uri": "spotify:track:1111111111111111111111", "artists": [{"name": "Drake"}]}},
    {"track": {"name": "Blinding Lights", "uri": "spotify:track:2222222222222222222222",
               "artists": [{"name": "The Weeknd"}]}},
    {"track": {"name": "Starboy", "uri": "spotify:track:3333333333333333333333",
               "artists": [{"name": "The Weeknd"}, {"name": "Daft Punk"}]}},
    {"track": None}], "next": None}


def playlist_http(req):
    if req.url.path.startswith("/v1/me/player/play"):
        return httpx.Response(204)
    if req.url.path == f"/v1/playlists/{PLAYLIST}":
        return httpx.Response(200, json={"name": "i love music"})
    if "/playlists/" in req.url.path:
        assert PLAYLIST in req.url.path and req.url.path.endswith("/items")
        return httpx.Response(200, json=LIST)
    return httpx.Response(200, json={"tracks": {"items": [
        {"uri": URI, "name": "Search Result", "artists": [{"name": "Somebody"}]}]}})


@pytest.mark.parametrize("value", [LINK, f"spotify:playlist:{PLAYLIST}", PLAYLIST,
                                   f"https://open.spotify.com/playlist/{PLAYLIST}"])
def test_playlist_ids_are_read_from_links_uris_and_bare_ids(value):
    from minty_core.plugins.builtin.spotify.plugin import parse_playlist_id

    assert parse_playlist_id(value) == PLAYLIST
    assert parse_playlist_id("not a playlist") is None and parse_playlist_id("") is None


async def test_a_song_in_the_playlist_is_played_from_it_without_searching(tmp_path, monkeypatch):
    host, ctl, seen, _ = host_for(tmp_path, monkeypatch, http=playlist_http, playlist=LINK)
    out = await host.call("spotify_play", {"query": "play Blinding Lights"})
    assert out == "Playing Blinding Lights by The Weeknd from your playlist."
    play = [r for r in seen if r.url.path == "/v1/me/player/play"][0]
    import json as _json
    assert _json.loads(play.content) == {"context_uri": f"spotify:playlist:{PLAYLIST}",
                                         "offset": {"uri": "spotify:track:2222222222222222222222"}}
    assert play.headers["authorization"] == "Bearer tok"
    assert not any(c[2:3] == ("open",) for c in ctl.calls)  # never the context-less link method
    assert not any(r.url.path.endswith("/search") for r in seen)


async def test_a_song_not_in_the_playlist_falls_back_to_spotify_search(tmp_path, monkeypatch):
    host, ctl, seen, _ = host_for(tmp_path, monkeypatch, http=playlist_http, playlist=LINK)
    out = await host.call("spotify_play", {"query": "Bohemian Rhapsody Queen"})
    assert out == f"That isn't in your playlist, so playing Search Result by Somebody." or \
        out.startswith("That isn't in your playlist, so playing Search Result")
    import json as _json
    assert _json.loads([r for r in seen if r.url.path == "/v1/me/player/play"][0].content) == {"uris": [URI]}
    assert any(r.url.path.endswith("/search") for r in seen)


async def test_an_artist_name_plays_one_of_their_songs_from_the_playlist(tmp_path, monkeypatch):
    host, ctl, seen, _ = host_for(tmp_path, monkeypatch, http=playlist_http, playlist=LINK)
    out = await host.call("spotify_play", {"query": "something by the weeknd"})
    assert out.startswith("Playing") and "The Weeknd" in out and "from your playlist" in out
    import json as _json
    sent = [_json.loads(r.content) for r in seen if r.url.path == "/v1/me/player/play"]
    assert sent[0]["offset"]["uri"] in ("spotify:track:2222222222222222222222", "spotify:track:3333333333333333333333")


async def test_no_query_plays_the_playlist_shuffled(tmp_path, monkeypatch):
    host, ctl, _, _ = host_for(tmp_path, monkeypatch, http=playlist_http, playlist=LINK)
    assert await host.call("spotify_play", {}) == "Playing your playlist."
    assert ("-p", "spotify", "shuffle", "On") in ctl.calls
    assert not any(c[2:3] == ("open",) for c in ctl.calls)


async def test_an_unreadable_playlist_still_plays_via_search(tmp_path, monkeypatch):
    def http(req):
        if "/playlists/" in req.url.path:
            return httpx.Response(404)
        return playlist_http(req)

    host, ctl, _, _ = host_for(tmp_path, monkeypatch, http=http, playlist=LINK)
    out = await host.call("spotify_play", {"query": "Bohemian Rhapsody"})
    assert "couldn't read your playlist" in out and "playing Search Result" in out


async def test_playlist_is_fetched_once_then_cached(tmp_path, monkeypatch):
    host, _, seen, _ = host_for(tmp_path, monkeypatch, http=playlist_http, playlist=LINK)
    await host.call("spotify_play", {"query": "Shabang"})
    await host.call("spotify_play", {"query": "Starboy"})
    assert sum(r.url.path.endswith("/items") for r in seen) == 1


async def test_playlist_is_read_as_the_signed_in_user_not_with_the_app_token(tmp_path, monkeypatch):
    seen_auth = []

    def http(req):
        if req.url.host == "accounts.spotify.com":
            if b"refresh_token" in req.content:
                return httpx.Response(200, json={"access_token": "USER-TOKEN", "expires_in": 3600})
            return httpx.Response(200, json={"access_token": "APP-TOKEN", "expires_in": 3600})
        seen_auth.append((req.url.path, req.headers.get("authorization")))
        return playlist_http(req)

    http.accounts = True
    host, _, _, _ = host_for(tmp_path, monkeypatch, http=http, playlist=LINK)
    out = await host.call("spotify_play", {"query": "Shabang"})
    assert "from your playlist" in out
    assert {a for p, a in seen_auth if "/playlists/" in p} == {"Bearer USER-TOKEN"}


async def test_not_signed_in_is_reported_instead_of_silently_searching(tmp_path, monkeypatch):
    def http(req):
        if "/playlists/" in req.url.path:
            return httpx.Response(403)
        return playlist_http(req)

    host, _, _, _ = host_for(tmp_path, monkeypatch, http=http, signed_in=False, playlist=LINK)
    out = await host.call("spotify_play", {"query": "Shabang"})
    assert "couldn't read your playlist" in out and "spotify-login" in out


async def test_item_shaped_playlist_entries_are_understood(tmp_path, monkeypatch):
    def http(req):
        if req.url.path.endswith("/items"):
            return httpx.Response(200, json={"items": [{"item": {
                "name": "9", "uri": "spotify:track:4444444444444444444444", "artists": [{"name": "Drake"}]}}]})
        return playlist_http(req)

    host, ctl, _, _ = host_for(tmp_path, monkeypatch, http=http, playlist=LINK)
    assert await host.call("spotify_play", {"query": "9 drake"}) == "Playing 9 by Drake from your playlist."


async def test_if_web_playback_is_refused_the_desktop_link_is_the_fallback(tmp_path, monkeypatch):
    def http(req):
        if req.url.path.startswith("/v1/me/player/play"):
            return httpx.Response(403, json={"error": {"reason": "PREMIUM_REQUIRED"}})
        return playlist_http(req)

    host, ctl, _, _ = host_for(tmp_path, monkeypatch, http=http, playlist=LINK)
    out = await host.call("spotify_play", {"query": "Blinding Lights"})
    assert out == "Playing Blinding Lights by The Weeknd from your playlist."
    assert ("-p", "spotify", "open", "spotify:track:2222222222222222222222") in ctl.calls


@pytest.mark.parametrize("args", [{"query": "music", "kind": "playlist"}, {"query": "some songs"},
                                  {"query": "play something", "kind": "playlist"}, {"query": "spotify"}])
async def test_generic_requests_play_the_users_playlist_never_a_random_search_result(tmp_path, monkeypatch, args):
    host, ctl, seen, _ = host_for(tmp_path, monkeypatch, http=playlist_http, playlist=LINK)
    out = await host.call("spotify_play", args)
    assert out == "Playing your playlist."
    plays = [r for r in seen if r.url.path == "/v1/me/player/play"]
    import json as _json
    assert _json.loads(plays[0].content) == {"context_uri": f"spotify:playlist:{PLAYLIST}"}
    assert not any(r.url.path.endswith("/search") for r in seen)


async def test_asking_for_a_playlist_or_album_still_prefers_the_users_playlist_songs(tmp_path, monkeypatch):
    host, ctl, seen, _ = host_for(tmp_path, monkeypatch, http=playlist_http, playlist=LINK)
    out = await host.call("spotify_play", {"query": "Starboy", "kind": "album"})
    assert out.startswith("Playing Starboy") and "from your playlist" in out
    assert not any(r.url.path.endswith("/search") for r in seen)


async def test_fallback_search_plays_one_track_never_a_whole_foreign_playlist(tmp_path, monkeypatch):
    searched = []

    def http(req):
        if req.url.path.endswith("/search"):
            searched.append(req.url.params["type"])
        return playlist_http(req)

    host, ctl, seen, _ = host_for(tmp_path, monkeypatch, http=http, playlist=LINK)
    await host.call("spotify_play", {"query": "Bohemian Rhapsody Queen", "kind": "playlist"})
    assert searched == ["track"]
    import json as _json
    sent = _json.loads([r for r in seen if r.url.path == "/v1/me/player/play"][0].content)
    assert sent == {"uris": [URI]}


@pytest.mark.parametrize("query", ["I Love Music", "i love music playlist", "my I love music"])
async def test_asking_for_the_playlist_by_its_name_plays_it(tmp_path, monkeypatch, query):
    host, ctl, seen, _ = host_for(tmp_path, monkeypatch, http=playlist_http, playlist=LINK)
    assert await host.call("spotify_play", {"query": query, "kind": "playlist"}) == "Playing your playlist."
    assert not any(r.url.path.endswith("/search") for r in seen)


async def test_idle_spotify_is_woken_through_its_device_not_a_desktop_link(tmp_path, monkeypatch):
    import json as _json
    import socket

    calls = []

    def http(req):
        if req.url.path == "/v1/me/player/play":
            calls.append(dict(req.url.params))
            if "device_id" not in req.url.params:
                return httpx.Response(404, json={"error": {"reason": "NO_ACTIVE_DEVICE"}})
            return httpx.Response(204)
        if req.url.path == "/v1/me/player/devices":
            return httpx.Response(200, json={"devices": [
                {"id": "phone-1", "name": "Pixel", "type": "Smartphone", "is_active": False},
                {"id": "pc-1", "name": socket.gethostname(), "type": "Computer", "is_active": False}]})
        return playlist_http(req)

    host, ctl, seen, opened = host_for(tmp_path, monkeypatch, http=http, playlist=LINK)
    out = await host.call("spotify_play", {"query": "Shabang"})
    assert out == "Playing Shabang by Drake from your playlist."
    assert calls == [{}, {"device_id": "pc-1"}] and opened == []  # nothing launched, no xdg-open


def test_no_desktop_link_handler_is_ever_used():
    from pathlib import Path

    import minty_core.plugins.builtin.spotify.plugin as plugin

    source = Path(plugin.__file__).read_text()
    assert '"xdg-open"' not in source
