"""The album cover in the music card, drawn with half-block pixels so it works in any terminal.

The picture's address comes from the media player (MPRIS `mpris:artUrl`): a local file or a web address. Web addresses go
through the same public-address check as every other fetch (a player cannot point Minty at your router or at localhost),
only images are read, and sizes are capped. ffmpeg (already used for voice notes) crops the picture square and shrinks it to
the cell grid, so no image library is needed; without ffmpeg, or while the picture loads, a coloured tile with a note on it is
shown instead (a different colour per track). Nothing here ever blocks the display: the picture arrives later.
"""
from __future__ import annotations

import asyncio
import colorsys
import hashlib
import subprocess
import time
from collections import OrderedDict
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import unquote, urlsplit

from rich.text import Text

from minty_core.gfx import Picture
from minty_core.safefetch import safe_fetch

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_FILE = 5_000_000
MAX_WEB = 3_000_000
RETRY_S = 60.0  # a picture that failed is tried again after this long


def _hsv(r: float, g: float, b: float):
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)


def _lift(h: float, s: float, v: float) -> tuple[int, int, int]:
    """A vivid colour of this hue that reads well on a dark screen."""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, min(0.95, max(0.55, s)), min(1.0, max(0.8, v)))
    return int(r * 255), int(g * 255), int(b * 255)


def _clusters(rgb: bytes) -> list[tuple[int, tuple[float, float, float], int]]:
    """The picture's colours by how many pixels use them, most used first: (pixels, average colour, hue bin). Black, white and
    grey (little colour or very dark) are left out. Pixels are grouped by hue (18 bins), lightness (3) and softness (2), so a
    sky blue and a navy are two colours, but the tones of one blue are one."""
    bins: dict[tuple[int, int, int], list[float]] = {}
    for i in range(0, len(rgb) - 2, 3):
        r, g, b = rgb[i], rgb[i + 1], rgb[i + 2]
        h, s, v = _hsv(r, g, b)
        if s < 0.18 or v < 0.15:
            continue
        key = (min(17, int(h * 18)), 0 if v < 0.4 else 1 if v < 0.75 else 2, 0 if s < 0.45 else 1)
        e = bins.setdefault(key, [0.0, 0.0, 0.0, 0.0])
        e[0] += 1
        e[1] += r
        e[2] += g
        e[3] += b
    ranked = sorted(bins.items(), key=lambda kv: kv[1][0], reverse=True)
    return [(int(e[0]), (e[1] / e[0], e[2] / e[0], e[3] / e[0]), key[0]) for key, e in ranked]


MIN_COLOUR_SHARE = 0.04  # a picture needs this much colour (not grey, white or black) to have an accent: JPEG noise does not count


def dominant_color(rgb: bytes) -> tuple[int, int, int] | None:
    """The accent colour of a picture: the colour used by the most pixels, skipping black, white and grey, as it really is (the
    average of that area); only a colour too dark to read on a dark screen is brightened, keeping its hue. None for a grey,
    black, white or empty picture (the default colour stays)."""
    clusters = _clusters(rgb)
    if not clusters or sum(c[0] for c in clusters) < MIN_COLOUR_SHARE * (len(rgb) // 3):
        return None
    r, g, b = clusters[0][1]
    top = max(r, g, b) / 255
    if top < 0.5:  # too dark to read: scale it up to a readable brightness, same hue and softness
        k = 0.62 / top
        r, g, b = min(255.0, r * k), min(255.0, g * k), min(255.0, b * k)
    return int(round(r)), int(round(g)), int(round(b))


def tile_accent(seed: str) -> tuple[int, int, int]:
    """The accent that matches the placeholder tile of a track (same hue), so the colours do not jump when art arrives."""
    hue = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6], 16) % 360 / 360
    return _lift(hue, 0.7, 1.0)


def to_text(rgb: bytes, cols: int, rows: int) -> Text:
    """cols x (rows*2) pixels of RGB bytes as `rows` lines of "▀" cells (top pixel in the colour, bottom as background)."""
    out = Text()
    for row in range(rows):
        for col in range(cols):
            t = (2 * row * cols + col) * 3
            b = ((2 * row + 1) * cols + col) * 3
            out.append("▀", style=f"rgb({rgb[t]},{rgb[t + 1]},{rgb[t + 2]}) on rgb({rgb[b]},{rgb[b + 1]},{rgb[b + 2]})")
        if row < rows - 1:
            out.append("\n")
    return out


def tile(seed: str, cols: int, rows: int) -> Text:
    """A steady placeholder: a soft gradient whose colour comes from the track, with a note in the middle."""
    hue = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6], 16) % 360 / 360
    out = Text()
    for row in range(rows):
        for col in range(cols):
            def colour(y: int) -> str:
                depth = (y + col * 0.4) / (rows * 2 + cols * 0.4)
                r, g, b = colorsys.hsv_to_rgb((hue + 0.08 * depth) % 1.0, 0.55, 0.62 - 0.30 * depth)
                return f"rgb({int(r * 255)},{int(g * 255)},{int(b * 255)})"

            top, bottom = colour(2 * row), colour(2 * row + 1)
            if row == rows // 2 and col == cols // 2:
                out.append("♪", style=f"bold white on {top}")
            else:
                out.append("▀", style=f"{top} on {bottom}")
        if row < rows - 1:
            out.append("\n")
    return out


async def art_bytes(url: str) -> bytes | None:
    """The picture at a player's art address, or None (not an image, too big, unreachable, not allowed)."""
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith("/"):
        path = Path(url)
    else:
        parts = urlsplit(url)
        if parts.scheme == "file":
            path = Path(unquote(parts.path))
        elif parts.scheme in ("http", "https"):
            try:
                got = await safe_fetch("GET", url, max_bytes=MAX_WEB)
            except Exception:  # noqa: BLE001 - blocked, unreachable or too slow: no picture
                return None
            ok = getattr(got, "status", 0) == 200 and str(getattr(got, "content_type", "")).lower().startswith("image/")
            return got.data if ok else None
        else:
            return None
    try:
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file() or path.stat().st_size > MAX_FILE:
            return None
        return await asyncio.to_thread(path.read_bytes)
    except OSError:
        return None


def _ffmpeg(data: bytes, vf: str, tail: list[str], run) -> bytes | None:
    cmd = ["ffmpeg", "-v", "error", "-i", "pipe:0", "-vf", vf, "-frames:v", "1", *tail, "pipe:1"]
    try:
        done = run(cmd, input=data, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def decode_pixels(data: bytes, width: int, height: int, run=subprocess.run) -> bytes | None:
    """Crop to a square and shrink to width x height RGB pixels with ffmpeg; None if it cannot."""
    out = _ffmpeg(data, f"crop='min(iw,ih)':'min(iw,ih)',scale={width}:{height}:flags=lanczos",
                  ["-f", "rawvideo", "-pix_fmt", "rgb24"], run)
    return out if out is not None and len(out) == width * height * 3 else None


def to_png(data: bytes, width: int, height: int, run=subprocess.run) -> bytes | None:
    """The same square crop as a PNG, for terminals that take a PNG (kitty)."""
    out = _ffmpeg(data, f"crop='min(iw,ih)':'min(iw,ih)',scale={width}:{height}:flags=lanczos",
                  ["-f", "image2pipe", "-c:v", "png"], run)
    return out if out and out.startswith(b"\x89PNG") else None


def decode(data: bytes, cols: int, rows: int, run=subprocess.run) -> bytes | None:
    """cols x (rows*2) RGB pixels for the half-block picture."""
    return decode_pixels(data, cols, rows * 2, run)


class CoverCache:
    """Covers by address, a few kept. `get` returns at once: the tile now, the picture once it has loaded."""

    def __init__(self, cols: int = 18, rows: int = 9, fetch: Callable[[str], Awaitable[bytes | None]] = art_bytes,
                 decode=decode, clock: Callable[[], float] = time.monotonic, keep: int = 8,
                 decode_px=decode_pixels, png=to_png):
        self.cols, self.rows, self._fetch, self._decode, self._clock, self._keep = cols, rows, fetch, decode, clock, keep
        self._decode_px, self._png = decode_px, png
        self._raw: OrderedDict[str, bytes] = OrderedDict()  # the downloaded files, a few, to make other sizes from
        self._big: OrderedDict[tuple, Picture] = OrderedDict()
        self._pending_big: set[tuple] = set()
        self._art: OrderedDict[tuple, Text] = OrderedDict()  # (address, cols, rows) -> half-block cover
        self._pending: set[tuple] = set()
        self._failed: dict = {}
        self._accent: dict[str, tuple[int, int, int] | None] = {}  # address -> accent colour of the picture
        self._tasks: set[asyncio.Task] = set()

    def get(self, track: dict, cols: int | None = None, rows: int | None = None) -> Text:
        cols, rows = cols or self.cols, rows or self.rows
        url = str(track.get("art_url") or "").strip()
        key = (url, cols, rows)
        if url:
            if key in self._art:
                self._art.move_to_end(key)
                return self._art[key]
            if key not in self._pending and self._clock() - self._failed.get(key, -1e9) >= RETRY_S:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None  # no event loop (a test, a one-off render): the tile will do
                if loop is not None:
                    self._pending.add(key)
                    task = loop.create_task(self._load(key))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        return tile(f"{track.get('artist', '')} {track.get('title', '')}", cols, rows)

    def accent(self, track: dict) -> tuple[int, int, int] | None:
        """The colour of this track: its cover's accent once loaded (None: a grey cover, keep the default colour), the
        colour of its placeholder tile until then or when it has no cover."""
        url = str(track.get("art_url") or "").strip()
        if url in self._accent:
            return self._accent[url]
        return tile_accent(f"{track.get('artist', '')} {track.get('title', '')}")

    async def _load(self, key: tuple) -> None:
        url, cols, rows = key
        try:
            data = self._raw.get(url) or await self._fetch(url)
            if data:
                self._raw[url] = data
                while len(self._raw) > 3:
                    self._raw.popitem(last=False)
            rgb = await asyncio.to_thread(self._decode, data, cols, rows) if data else None
            if rgb:
                self._art[key] = to_text(rgb, cols, rows)
                self._accent[url] = dominant_color(rgb)
                while len(self._art) > self._keep:
                    self._art.popitem(last=False)
            else:
                self._failed[key] = self._clock()
        except Exception:  # noqa: BLE001 - the display never depends on a picture
            self._failed[key] = self._clock()
        finally:
            self._pending.discard(key)

    def picture(self, track: dict, width: int, height: int, protocol: str | None) -> Picture | None:
        """The cover at `width` x `height` pixels for a terminal that shows real pictures: None until it has loaded."""
        url = str(track.get("art_url") or "").strip()
        if not url or protocol not in ("sixel", "kitty"):
            return None
        key = (url, width, height, protocol)
        if key in self._big:
            self._big.move_to_end(key)
            return self._big[key]
        if key not in self._pending_big and self._clock() - self._failed.get(key, -1e9) >= RETRY_S:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return None
            self._pending_big.add(key)
            task = loop.create_task(self._load_big(key))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return None

    async def _load_big(self, key: tuple) -> None:
        url, width, height, protocol = key
        try:
            data = self._raw.get(url) or await self._fetch(url)
            if data:
                self._raw[url] = data
                while len(self._raw) > 3:
                    self._raw.popitem(last=False)
            name = f"{url}@{width}x{height}/{protocol}"
            picture = None
            if data and protocol == "sixel":
                rgb = await asyncio.to_thread(self._decode_px, data, width, height)
                if rgb:
                    picture = Picture(name, width, height, rgb)
                    await asyncio.to_thread(picture.sixel_text)  # the slow part, off the display's loop
            elif data:
                png = await asyncio.to_thread(self._png, data, width, height)
                if png:
                    picture = Picture(name, width, height, b"", png)
            if picture is not None:
                self._big[key] = picture
                while len(self._big) > self._keep:
                    self._big.popitem(last=False)
            else:
                self._failed[key] = self._clock()
        except Exception:  # noqa: BLE001 - the display never depends on a picture
            self._failed[key] = self._clock()
        finally:
            self._pending_big.discard(key)
