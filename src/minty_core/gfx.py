"""Real pictures in the terminal, where the terminal can show them.

Two protocols cover nearly every modern terminal: the kitty graphics protocol (kitty, WezTerm, Ghostty) and sixel (GNOME
Terminal and other VTE terminals, foot, xterm, Konsole, WezTerm, Windows Terminal). Which one is used is asked of the
terminal once, at start; a terminal that speaks neither keeps the half-block pictures (audio/cover.py). `MINTY_IMAGES=off`
turns pictures off, `MINTY_IMAGES=sixel` or `kitty` forces one.

Pictures are drawn after the text of each frame at a cell position the layout reserved (spaces), so the display itself never
depends on them. Sixel pixels are part of the text cells, so they are drawn again after every frame; a kitty picture lives in
its own layer behind the text and is only sent when it changes or moves.
"""
from __future__ import annotations

import base64
import itertools
import os
import select
import struct
import sys
from dataclasses import dataclass, field
from typing import Callable

try:  # the terminal can only be questioned through these, which Windows does not have: there, only what the terminal says about itself (or MINTY_IMAGES) is used
    import fcntl
    import termios
except ImportError:  # pragma: no cover — exercised by the simulated-Windows test
    fcntl = termios = None  # type: ignore[assignment]

ESC = "\x1b"
KITTY_ID = 4242  # the one picture Minty keeps on screen
KITTY_QUERY = f"{ESC}_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA{ESC}\\"
DA1_QUERY = f"{ESC}[c"
DEFAULT_CELL = (9, 18)  # pixels, when the terminal does not say


def parse_reply(data: bytes) -> str | None:
    """"kitty" when the terminal answered the graphics query, "sixel" when its device attributes list feature 4."""
    if b"\x1b_Gi=31;OK" in data:
        return "kitty"
    import re

    m = re.search(rb"\x1b\[\?([0-9;]*)c", data)
    if m and b"4" in m.group(1).split(b";"):
        return "sixel"
    return None


def env_protocol(env) -> str | None:
    """Terminals that say who they are need no question."""
    if env.get("TERM") == "xterm-kitty" or env.get("KITTY_WINDOW_ID") or env.get("TERM_PROGRAM") in ("ghostty", "WezTerm"):
        return "kitty"
    return None


def _ask_terminal(timeout: float = 0.6) -> bytes:
    """Send the two questions and collect the answer (the device-attributes reply always comes last)."""
    if termios is None:
        return b""
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return b""
    old = termios.tcgetattr(fd)
    got = b""
    try:
        import tty

        tty.setcbreak(fd)
        os.write(fd, (KITTY_QUERY + DA1_QUERY).encode())
        end = _now() + timeout
        while _now() < end:
            ready, _, _ = select.select([fd], [], [], max(0.0, end - _now()))
            if not ready:
                break
            got += os.read(fd, 4096)
            if b"c" in got.rsplit(b"\x1b[?", 1)[-1] and b"\x1b[?" in got:
                break
    except (OSError, termios.error):
        return got
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        os.close(fd)
    return got


def _now() -> float:
    import time

    return time.monotonic()


def detect(env=None, ask: Callable[[], bytes] | None = None, is_tty: bool | None = None) -> str | None:
    env = os.environ if env is None else env
    choice = str(env.get("MINTY_IMAGES", "")).strip().lower()
    if choice in ("off", "0", "no", "none"):
        return None
    if choice in ("sixel", "kitty"):
        return choice
    tty = (sys.stdin.isatty() and sys.stdout.isatty()) if is_tty is None else is_tty
    if not tty or env.get("TERM", "") in ("", "dumb"):
        return None
    hint = env_protocol(env)
    if hint:
        return hint
    return parse_reply((ask or _ask_terminal)())


def cell_pixels(fd: int, ioctl=None) -> tuple[int, int]:
    """Width and height of one character cell in pixels, from the window size when the terminal reports it."""
    if ioctl is None:
        if fcntl is None:
            return DEFAULT_CELL
        ioctl = fcntl.ioctl
    try:
        rows, cols, xpixel, ypixel = struct.unpack("HHHH", ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0)))
    except (OSError, struct.error):
        return DEFAULT_CELL
    if rows and cols and xpixel and ypixel:
        return xpixel // cols, ypixel // rows
    return DEFAULT_CELL


# -- sixel -------------------------------------------------------------------------------------------------------------

def _palette(px, colors: int):
    import numpy as np

    flat = px.reshape(-1, 3)
    unique = np.unique(flat, axis=0)
    if len(unique) <= colors:
        return unique.astype(np.uint8)
    q = (flat >> 4).astype(np.int32)
    key = q[:, 0] << 8 | q[:, 1] << 4 | q[:, 2]  # a 12-bit colour: the popular ones become the palette
    counts = np.bincount(key, minlength=4096)
    top = np.argsort(counts)[::-1][:colors]
    top = top[counts[top] > 0]
    out = []
    for k in top:
        out.append(flat[key == k].mean(axis=0))
    return np.array(out).round().astype(np.uint8)


def encode_sixel(rgb: bytes, width: int, height: int, colors: int = 96) -> str:
    """The picture as a sixel string (a palette of at most `colors`, runs compressed)."""
    import numpy as np

    px = np.frombuffer(rgb, dtype=np.uint8).reshape(height, width, 3)
    palette = _palette(px, colors)
    flat = px.reshape(-1, 3).astype(np.int32)
    pal = palette.astype(np.int32)
    idx = np.empty(len(flat), dtype=np.int32)
    for start in range(0, len(flat), 8192):  # nearest palette colour, in blocks to keep memory small
        block = flat[start:start + 8192]
        idx[start:start + 8192] = ((block[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    idx = idx.reshape(height, width)
    out = [f"{ESC}P0;1;0q", f'"1;1;{width};{height}']
    for i, (r, g, b) in enumerate(palette):
        out.append(f"#{i};2;{round(int(r) * 100 / 255)};{round(int(g) * 100 / 255)};{round(int(b) * 100 / 255)}")
    weights = (1 << np.arange(6))[:, None]
    bands = -(-height // 6)
    for band in range(bands):
        rows = idx[band * 6:band * 6 + 6]
        w = weights[:len(rows)]
        parts = []
        for colour in np.unique(rows):
            bits = ((rows == colour) * w).sum(axis=0)
            chars = "".join(chr(63 + int(v)) for v in bits)
            runs = []
            for ch, group in itertools.groupby(chars):
                n = len(list(group))
                runs.append(f"!{n}{ch}" if n > 3 else ch * n)
            parts.append(f"#{int(colour)}{''.join(runs)}")
        out.append("$".join(parts) + ("-" if band < bands - 1 else ""))
    out.append(f"{ESC}\\")
    return "".join(out)


# -- kitty -------------------------------------------------------------------------------------------------------------

def kitty_show(png: bytes, image_id: int, cols: int, rows: int) -> str:
    """Transmit and display a PNG over `cols` x `rows` cells at the cursor, behind the text (z=-1), no replies wanted."""
    data = base64.b64encode(png).decode("ascii")
    chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)] or [""]
    out = []
    for n, chunk in enumerate(chunks):
        more = 1 if n < len(chunks) - 1 else 0
        keys = f"a=T,f=100,i={image_id},c={cols},r={rows},z=-1,q=2,m={more}" if n == 0 else f"m={more}"
        out.append(f"{ESC}_G{keys};{chunk}{ESC}\\")
    return "".join(out)


def kitty_delete(image_id: int) -> str:
    return f"{ESC}_Ga=d,d=i,i={image_id},q=2{ESC}\\"


# -- drawing -----------------------------------------------------------------------------------------------------------

@dataclass
class Graphics:
    """What the terminal can show: the protocol and the size of one character cell in pixels."""
    protocol: str
    cell_w: int
    cell_h: int


@dataclass
class Picture:
    """A cover ready to draw: its identity, size in pixels, RGB pixels (sixel) and a PNG of it (kitty)."""
    key: str
    width: int
    height: int
    rgb: bytes
    png: bytes = b""
    sixel: str = field(default="", repr=False)

    def sixel_text(self) -> str:
        if not self.sixel:
            self.sixel = encode_sixel(self.rgb, self.width, self.height)
        return self.sixel


class ImageOverlay:
    """Puts a picture over a block of reserved cells. `write` gets raw terminal text."""

    def __init__(self, write: Callable[[str], None], protocol: str | None):
        self._write, self.protocol = write, protocol
        self._shown: tuple | None = None  # kitty only: what is on screen now

    def show(self, picture: Picture | None, x: int, y: int, cols: int, rows: int) -> None:
        if self.protocol is None:
            return
        if picture is None:
            self._remove()
            return
        goto = f"{ESC}[{y + 1};{x + 1}H"
        if self.protocol == "sixel":
            self._write(f"{ESC}7{goto}{picture.sixel_text()}{ESC}8")  # painted over by the next frame's text: draw each time
            return
        state = (picture.key, x, y, cols, rows)
        if state == self._shown:
            return
        self._remove()
        self._write(f"{ESC}7{goto}{kitty_show(picture.png, KITTY_ID, cols, rows)}{ESC}8")
        self._shown = state

    def _remove(self) -> None:
        if self.protocol == "kitty" and self._shown is not None:
            self._write(kitty_delete(KITTY_ID))
        self._shown = None

    def clear(self) -> None:
        self._remove()
