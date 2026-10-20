"""The album cover in the music card: art from the player, shrunk by ffmpeg, drawn in half-block pixels; a tile otherwise."""
import asyncio
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from minty_core.audio.cover import CoverCache, art_bytes, decode, tile, to_text


def png(width, height, pixels):
    """A tiny valid PNG (8-bit RGB) from a flat list of (r, g, b)."""
    raw = b"".join(b"\x00" + b"".join(bytes(pixels[y * width + x]) for x in range(width)) for y in range(height))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# -- drawing ----------------------------------------------------------------------------------------------------------

def test_two_stacked_pixels_become_one_half_block_cell():
    rgb = bytes([255, 0, 0, 0, 0, 255,   # top row: red, blue
                 0, 255, 0, 10, 20, 30])  # bottom row: green, dark
    t = to_text(rgb, cols=2, rows=1)
    assert t.plain == "▀▀"
    styles = [str(s.style) for s in t.spans]
    assert "rgb(255,0,0) on rgb(0,255,0)" in styles and "rgb(0,0,255) on rgb(10,20,30)" in styles


def test_the_placeholder_tile_is_the_right_size_steady_and_per_track():
    a = tile("Daft Punk Veridis Quo", 12, 6)
    lines = a.plain.split("\n")
    assert len(lines) == 6 and all(len(line) == 12 for line in lines) and "♪" in a.plain
    assert a.plain == tile("Daft Punk Veridis Quo", 12, 6).plain
    assert [str(s.style) for s in a.spans] == [str(s.style) for s in tile("Daft Punk Veridis Quo", 12, 6).spans]
    assert [str(s.style) for s in a.spans] != [str(s.style) for s in tile("Someone Else", 12, 6).spans]


# -- getting the picture ------------------------------------------------------------------------------------------------

async def test_a_local_image_file_is_read_and_anything_else_is_refused(tmp_path):
    good = tmp_path / "cover.jpg"
    good.write_bytes(b"\xff\xd8 not really a jpeg")
    assert await art_bytes(good.as_uri()) == b"\xff\xd8 not really a jpeg"
    assert await art_bytes(str(good)) == b"\xff\xd8 not really a jpeg"  # a plain path works too
    secret = tmp_path / "keys.toml"
    secret.write_text("groq = 'x'")
    assert await art_bytes(secret.as_uri()) is None  # not an image: never read
    assert await art_bytes((tmp_path / "missing.png").as_uri()) is None
    big = tmp_path / "big.png"
    big.write_bytes(b"0" * 6_000_000)
    assert await art_bytes(big.as_uri()) is None
    assert await art_bytes("data:image/png;base64,AAAA") is None and await art_bytes("") is None
    assert await art_bytes("ftp://example.com/a.jpg") is None


async def test_a_web_address_goes_through_the_public_address_check(monkeypatch):
    seen = []

    async def fake(method, url, **kw):
        seen.append((method, url, kw.get("max_bytes")))
        return type("F", (), {"status": 200, "content_type": "image/jpeg", "data": b"IMG"})()

    monkeypatch.setattr("minty_core.audio.cover.safe_fetch", fake)
    assert await art_bytes("https://i.scdn.co/image/abc") == b"IMG"
    assert seen == [("GET", "https://i.scdn.co/image/abc", 3_000_000)]

    async def bad(method, url, **kw):
        raise ValueError("blocked")  # a player pointing at 127.0.0.1 or a LAN address

    monkeypatch.setattr("minty_core.audio.cover.safe_fetch", bad)
    assert await art_bytes("http://127.0.0.1:8080/cover.jpg") is None

    async def html(method, url, **kw):
        return type("F", (), {"status": 200, "content_type": "text/html", "data": b"<html>"})()

    monkeypatch.setattr("minty_core.audio.cover.safe_fetch", html)
    assert await art_bytes("https://example.com/not-an-image") is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_shrinks_a_real_picture_to_the_cell_grid():
    red, blue = (255, 0, 0), (0, 0, 255)
    data = png(4, 4, [red] * 8 + [blue] * 8)  # top half red, bottom half blue
    rgb = decode(data, cols=2, rows=1)
    assert rgb is not None and len(rgb) == 2 * 2 * 3
    assert rgb[0] > 200 and rgb[2] < 60  # the top pixel is red
    assert rgb[-3] < 60 and rgb[-1] > 200  # the bottom pixel is blue
    assert decode(b"this is not a picture", cols=2, rows=1) is None


def test_a_missing_ffmpeg_means_no_picture_not_a_crash():
    def run(*a, **k):
        raise FileNotFoundError

    assert decode(b"x", 2, 1, run=run) is None


# -- the cache ----------------------------------------------------------------------------------------------------------

TRACK = {"title": "Veridis Quo", "artist": "Daft Punk", "art_url": "https://i.scdn.co/image/abc"}


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)


async def test_the_tile_shows_at_once_and_the_picture_replaces_it_when_it_arrives():
    fetched = []

    async def fetch(url):
        fetched.append(url)
        return b"IMG"

    cache = CoverCache(cols=2, rows=1, fetch=fetch, decode=lambda data, c, r: bytes([9, 9, 9] * 4))
    first = cache.get(TRACK)
    assert first.plain != "" and not any("rgb(9,9,9)" in str(s.style) for s in first.spans)  # the tile, for now
    await settle()
    later = cache.get(TRACK)
    assert any("rgb(9,9,9)" in str(s.style) for s in later.spans)
    cache.get(TRACK)
    await settle()
    assert fetched == [TRACK["art_url"]]  # one download per picture


async def test_a_failed_picture_keeps_the_tile_and_is_not_retried_every_frame():
    calls, clock = [], [0.0]

    async def fetch(url):
        calls.append(url)
        return None

    cache = CoverCache(cols=2, rows=1, fetch=fetch, decode=lambda *a: None, clock=lambda: clock[0])
    for _ in range(4):
        cache.get(TRACK)
        await settle()
    assert len(calls) == 1
    clock[0] = 120.0  # a minute later it may try again
    cache.get(TRACK)
    await settle()
    assert len(calls) == 2


def test_without_a_running_loop_or_a_picture_address_it_just_gives_the_tile():
    cache = CoverCache(cols=2, rows=1)
    assert cache.get(TRACK).plain and cache.get({"title": "A stream", "artist": "", "art_url": ""}).plain


async def test_the_cache_is_small():
    async def fetch(url):
        return b"x"

    cache = CoverCache(cols=2, rows=1, fetch=fetch, decode=lambda *a: bytes(12), keep=3)
    for n in range(6):
        cache.get({"title": str(n), "artist": "", "art_url": f"https://x/{n}"})
        await settle()
    assert len(cache._art) <= 3


# -- the large picture for terminals that can show one -------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_makes_pixels_of_any_size_and_a_png():
    from minty_core.audio.cover import decode_pixels, to_png

    data = png(4, 4, [(255, 0, 0)] * 16)
    rgb = decode_pixels(data, 8, 6)
    assert rgb is not None and len(rgb) == 8 * 6 * 3 and rgb[0] > 200
    out = to_png(data, 8, 6)
    assert out is not None and out.startswith(b"\x89PNG")
    assert decode_pixels(b"junk", 8, 6) is None and to_png(b"junk", 8, 6) is None


async def until(fn, timeout=5.0):
    """Wait for a background load (numpy and ffmpeg start-up can take a moment the first time)."""
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        got = fn()
        if got is not None:
            return got
        await asyncio.sleep(0.02)
    return None


async def test_the_large_picture_loads_in_the_background_for_the_protocol_in_use():
    fetched = []

    async def fetch(url):
        fetched.append(url)
        return b"IMG"

    cache = CoverCache(cols=2, rows=1, fetch=fetch, decode=lambda *a: None,
                       decode_px=lambda data, w, h: bytes([5, 6, 7] * (w * h)), png=lambda data, w, h: b"\x89PNG-" + bytes([w, h]))
    assert cache.picture(TRACK, 16, 12, "sixel") is None  # not yet
    p = await until(lambda: cache.picture(TRACK, 16, 12, "sixel"))
    assert p is not None and (p.width, p.height, len(p.rgb)) == (16, 12, 16 * 12 * 3) and p.sixel.startswith("\x1bP")
    assert cache.picture(TRACK, 16, 12, "sixel") is p and fetched == [TRACK["art_url"]]
    assert cache.picture(TRACK, 16, 12, "kitty") is None  # another protocol: its own picture
    k = await until(lambda: cache.picture(TRACK, 16, 12, "kitty"))
    assert k is not None and k.png.startswith(b"\x89PNG") and k.rgb == b"" and k.key != p.key
    assert fetched == [TRACK["art_url"]]  # the downloaded file is reused, not fetched again


async def test_a_picture_that_cannot_be_made_is_none_and_not_retried_every_frame():
    calls = []

    async def fetch(url):
        calls.append(url)
        return b"IMG"

    cache = CoverCache(cols=2, rows=1, fetch=fetch, decode=lambda *a: None, decode_px=lambda *a: None,
                       png=lambda *a: None, clock=lambda: 0.0)
    for _ in range(3):
        assert cache.picture(TRACK, 16, 12, "sixel") is None
        await settle()
    assert len(calls) <= 1
    assert cache.picture({"title": "x", "artist": "", "art_url": ""}, 16, 12, "sixel") is None


# -- the colour of a cover --------------------------------------------------------------------------------------------

def test_the_accent_is_the_colour_used_most_not_the_most_vivid_and_never_black_white_or_grey():
    from minty_core.audio.cover import dominant_color
    grey, red = bytes([120, 120, 120]), bytes([210, 30, 40])
    r, g, b = dominant_color(grey * 90 + red * 10)  # mostly grey: the grey is skipped, the red is chosen
    assert r > g and r > b and abs(r - 210) <= 6 and abs(g - 30) <= 6
    blue = bytes([20, 40, 200])
    r, g, b = dominant_color(red * 30 + blue * 70)  # the bigger area wins
    assert b > r and b > g
    pastel = bytes([150, 200, 230])  # a soft light blue used a lot beats a vivid red used a little
    r, g, b = dominant_color(pastel * 60 + red * 40)
    assert b > r and b > g and all(abs(a - c) <= 8 for a, c in zip((r, g, b), (150, 200, 230)))  # and it is the true colour


def test_when_the_most_used_colour_is_white_black_or_grey_the_next_one_is_chosen():
    from minty_core.audio.cover import dominant_color
    white, black, grey, orange = bytes([250, 250, 250]), bytes([8, 8, 8]), bytes([128, 128, 128]), bytes([240, 130, 30])
    r, g, b = dominant_color(white * 80 + black * 70 + orange * 10)
    assert r > b and r > g * 1.4 and abs(r - 240) <= 6
    r, g, b = dominant_color(grey * 50 + white * 30 + black * 20 + bytes([30, 160, 90]) * 5)
    assert g > r and g > b  # a little green after the greys, white and black


def test_the_accent_is_the_real_average_of_the_area_not_a_brightened_one():
    from minty_core.audio.cover import dominant_color
    near = [bytes([r, 190, 225]) for r in (140, 150, 160)]  # a sky blue with a little variation
    got = dominant_color(b"".join(c * 30 for c in near))
    assert all(abs(a - c) <= 6 for a, c in zip(got, (150, 190, 225)))


def test_a_very_dark_colour_is_lifted_just_enough_to_read_and_keeps_its_hue():
    from minty_core.audio.cover import dominant_color
    dark = dominant_color(bytes([60, 8, 14]) * 100)  # a deep red
    assert dark is not None and 140 <= max(dark) <= 175 and dark[0] > dark[1] * 3 and dark[0] > dark[2] * 3


def test_a_grey_or_empty_picture_has_no_accent_so_the_default_colour_stays():
    from minty_core.audio.cover import dominant_color
    assert dominant_color(bytes([128, 128, 128]) * 100) is None
    assert dominant_color(bytes([0, 0, 0]) * 100) is None and dominant_color(b"") is None


def test_the_tile_has_a_steady_accent_per_track():
    from minty_core.audio.cover import tile_accent
    assert tile_accent("a b") == tile_accent("a b") and tile_accent("a b") != tile_accent("c d")
    assert all(0 <= c <= 255 for c in tile_accent("a b")) and max(tile_accent("a b")) >= 190


async def test_the_cache_gives_the_tile_colour_while_loading_then_the_cover_colour():
    async def fetch(url):
        return b"IMG"

    cache = CoverCache(cols=2, rows=1, fetch=fetch, decode=lambda data, c, r: bytes([200, 20, 30] * (c * r * 2)))
    before = cache.accent(TRACK)
    assert before == __import__("minty_core.audio.cover", fromlist=["tile_accent"]).tile_accent("Daft Punk Veridis Quo")
    cache.get(TRACK)  # starts the load
    after = await until(lambda: cache.accent(TRACK) if cache.accent(TRACK) != before else None)
    assert after is not None and after[0] > after[1] and after[0] > after[2]  # the cover's own red
    assert cache.accent({"title": "x", "artist": "y", "art_url": ""}) is not None
