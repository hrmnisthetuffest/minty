#!/usr/bin/env python3
"""Look at the terminal display without starting Minty: sample data, any size.

  python scripts/tui_preview.py                       draw once in this terminal
  python scripts/tui_preview.py --live                animate it until Ctrl+C
  python scripts/tui_preview.py --svg out.svg -w 110  write a picture (open it in a browser)
Options: --state listening|speaking|thinking|idle|live, --noting, --paused, --nothing, --width, --height,
--images auto|off|sixel|kitty (real album cover in terminals that can show pictures; --live only),
--art FILE_OR_URL (use your own cover image for the sample track), --cover-size 28 (cells wide; 28 is about 256 pixels),
--sample-music (do not follow the song that is really playing; the card follows it by default when a player is running).
"""
import argparse
import asyncio
import struct
import sys
import time
import tempfile
import zlib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402

from minty_core.audio.music import MusicWatcher  # noqa: E402
from minty_core.tui import TuiModel, render  # noqa: E402

STATUS = {"voice_mode": "classic", "tier": "fast", "private": False, "music": True, "muted": False, "tts": "kokoro",
          "wake": "name", "brain_ready": 9, "brain_total": 9, "cooling": [], "context_msgs": 0, "rules": 12,
          "commands": 2, "subagents": [], "jobs": [], "model": "claude · haiku", "picked": "claude",
          "models": ["claude", "claude-deep", "groq", "gemini", "openrouter", "mistral", "nvidia"],
          "capacity": {"share": 0.38, "calls_left": 113, "forecast": "lasts until the daily reset"},
          "agenda": [{"kind": "timer", "text": "tea", "due": 0.0, "remaining_s": 272.0},
                     {"kind": "reminder", "text": "call dad", "due": time.time() + 5400, "remaining_s": 5400.0},
                     {"kind": "reminder", "text": "maths test revision", "due": time.time() + 90000, "remaining_s": 90000.0}],
          "now_playing": {"title": "Veridis Quo", "artist": "Daft Punk", "album": "Discovery", "status": "Playing",
                          "player": "spotify", "length_s": 345, "position_s": 83.0}}


def demo_cover() -> str:
    """A sample album cover (a warm gradient with a disc) written to a temp file; returns its file:// address."""
    size, rows = 96, []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            d = ((x - 48) ** 2 + (y - 48) ** 2) ** 0.5
            if d < 8:
                px = (20, 20, 24)
            elif 20 < d < 24 or 34 < d < 36:
                px = (250, 215, 150)
            else:
                px = (min(255, 60 + x * 2), 40 + y, min(255, 160 - y))
            row += bytes(px)
        rows.append(bytes(row))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))
    path = Path(tempfile.gettempdir()) / "minty-preview-cover.png"
    path.write_bytes(data)
    return path.as_uri()


def build(args) -> TuiModel:
    status = dict(STATUS)
    art = args.art.strip()
    if art and not art.startswith(("http://", "https://", "file://")):
        art = Path(art).expanduser().resolve().as_uri()
    status["now_playing"] = {**status["now_playing"], "art_url": art or demo_cover()}
    if args.paused:
        status["now_playing"] = {**status["now_playing"], "status": "Paused"}
    if args.nothing:
        status.update(music=False, now_playing=None)
    watcher = None if args.sample_music else MusicWatcher()  # the song that is really playing, from playerctl

    def current() -> dict:
        if watcher is None or args.nothing:
            return status
        try:
            track, playing = watcher.now_playing(), watcher.is_playing()
        except Exception:  # noqa: BLE001
            return status
        if track is None and not playing:
            return status  # no player running: keep the sample so the card can still be previewed
        return {**status, "music": playing or bool(track and track["status"] == "Playing"), "now_playing": track}

    m = TuiModel(status=current, flags=lambda: {})
    m.apply({"type": "state", "state": args.state})
    if args.typing:
        m.buffer = args.typing
    m.apply({"type": "level", "db": -38.0, "threshold_db": -40.0, "speech": False})
    for role, text in (("assistant", "I wasn't closed properly last time. Do you want to continue the previous conversation?"),
                       ("user", "yes, carry on"), ("assistant", "Picking up where we stopped: the study plan for Friday.")):
        m.apply({"type": "transcript", "role": role, "text": text})
    if args.state == "listening":
        m.apply({"type": "partial", "text": "what is the weather like in", "tentative": " Paris tomorrow"})
    if args.noting:
        m.apply({"type": "notes", "active": True, "items": ["buy milk and eggs", "call dad at five", "maths test on Friday"]})
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default="idle")
    ap.add_argument("--noting", action="store_true")
    ap.add_argument("--typing", default="", help="show this text as if typed in the chat bar")
    ap.add_argument("--paused", action="store_true")
    ap.add_argument("--nothing", action="store_true", help="nothing is playing")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--images", default="auto", choices=["auto", "off", "sixel", "kitty"])
    ap.add_argument("--sample-music", action="store_true", help="show the sample track even when a player is running")
    ap.add_argument("--art", default="", help="an image file or address to show as the cover (your own copy of an album cover)")
    ap.add_argument("--cover-size", type=int, default=18, help="the real cover's width in cells (28 is about 256 pixels)")
    ap.add_argument("--svg", default="")
    ap.add_argument("--width", "-w", type=int, default=0)
    ap.add_argument("--height", type=int, default=0)
    args = ap.parse_args()
    model = build(args)
    if args.svg:
        console = Console(record=True, width=args.width or 110, height=args.height or 36, force_terminal=True,
                          file=open("/dev/null", "w"))

        async def draw() -> None:
            wall = datetime(2026, 10, 19, 22, 47)
            render(model, console.width, console.height, now=1.5, wall=wall)  # starts loading the cover
            await asyncio.sleep(1.0)
            console.print(render(model, console.width, console.height, now=1.5, wall=wall))

        asyncio.run(draw())
        Path(args.svg).write_text(console.export_svg(title="Minty"))
        print("wrote", args.svg)
        return
    console = Console()
    width, height = args.width or console.width, args.height or console.height
    if not args.live:
        console.print(render(model, width, height - 1))
        return
    from minty_core.events import EventBus
    from minty_core.tui import TerminalUI

    bus = EventBus()

    async def echo(text: str) -> None:  # the preview has no assistant: it answers by repeating what you typed
        bus.publish("transcript", role="user", text=text)
        bus.publish("transcript", role="assistant", text=f"(preview) you typed: {text}")

    try:
        asyncio.run(TerminalUI(bus, model=model, images=lambda: args.images, cover_size=lambda: args.cover_size,
                               on_submit=echo).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
