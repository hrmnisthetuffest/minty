"""The terminal face of Minty: keep it open on the laptop. Shows what it is doing, live."""
import asyncio
import io
import logging
import math
import textwrap
import time
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.markdown import Markdown
from rich.align import Align
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from minty_core import __version__
from minty_core.audio.cover import CoverCache, tile
from minty_core.gfx import Graphics, ImageOverlay, cell_pixels, detect
from minty_core.audio.stable import Typewriter
from minty_core.timefmt import clock_text as time_text, is_24h

log = logging.getLogger(__name__)

COLORS = {"idle": "grey62", "listening": "cyan", "thinking": "yellow", "speaking": "green",
          "live": "magenta", "loading": "blue", "processing": "sky_blue2", "executing": "orange1", "error": "red"}
LABELS = {"idle": "WAITING FOR “MINTY”", "listening": "LISTENING", "thinking": "THINKING",
          "speaking": "SPEAKING", "live": "LIVE CONVERSATION", "loading": "LOADING MODELS",
          "processing": "WORKING ON IT", "executing": "RUNNING A TOOL", "error": "SOMETHING WENT WRONG"}
BARS = " ▁▂▃▄▅▆▇█"
ACCENT, ALPHA_RED, QUIET = "#ff7a2f", "#e5484d", "grey50"  # the same ember as the guided setup
WORDS_PER_S = 2.8  # how fast Minty's words appear in the chat bar, about the pace of the voice
SAID_LINGER_S = 10.0  # the finished sentence stays this long
MAX_TYPED = 500
CHAT_ROWS = 3  # the chat bar: a border, a line, a border
MUTED_RGB = (229, 72, 77)  # muted: the whole display takes this red
NOTICE_FOR_S = 12.0  # a warning or error is shown this long inside the header, then fades
COVER_COLS, COVER_ROWS = 18, 9  # the album cover: 18 x 18 pixels as half-blocks (a cell is two pixels tall)
G_COVER_COLS = 18  # the real picture's block: 18 x 9 cells, about 162 x 162 pixels (a cell is about 9 x 18); a setting
# The music card is a tall, narrow card: the square cover on top, then title, artist, progress and status underneath. It is as
# wide as the cover plus its border and padding, and only shown when the conversation keeps CONVO_MIN_WIDTH beside it.
CARD_TEXT_ROWS = 9  # heading, a row of air, a two-line title, artist and album, progress, the three-row play icon and equaliser
CARD_BORDER_ROWS = 2
TEXT_ONLY_W = 26  # a card too short for a cover is this wide
CARD_MIN_INNER = 18  # the text column is never narrower than this: a small cover is centred above it
CONVO_MIN_WIDTH = 50
CLOCK_MIN_WIDTH = 112


def _hex_rgb(value: str) -> tuple[int, int, int]:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _shade(rgb: tuple[float, float, float], keep: float) -> str:
    """The colour darkened to `keep` of its brightness, as a hex string (card borders, quiet accents)."""
    return "#" + "".join(f"{round(c * keep):02x}" for c in rgb)


class TuiModel:
    def __init__(self, flags=None, status=None):
        self.status = status or (lambda: {})
        self.state, self.db, self.threshold_db, self.speech = "loading", -90.0, -40.0, False
        self.caption, self.tentative = "", ""  # the words heard so far (and the recogniser's latest guess at the next ones)
        self.typing = Typewriter()  # words appear one by one, settled ones bright, the newest guess dim
        self.turns: deque = deque(maxlen=8)
        self.display_title, self.display_content = "", ""
        self.flags = flags or (lambda: {})
        self.level = 0.0  # smoothed 0..1
        self.reduced_motion = False
        self.notice, self.notice_at, self.notice_kind = "", 0.0, "notice"
        self.noting, self.notes = False, []  # "note for me" is on, and the latest notes
        self.covers = CoverCache(COVER_COLS, COVER_ROWS)  # album covers (and their colours) for the music card
        self.gfx: Graphics | None = None  # set when the terminal can show real pictures
        self.cover_cols = G_COVER_COLS  # width of the real cover in cells (its height is half)
        self.cover_at = None  # (picture, x, y, cols, rows): where the last frame wants the real cover drawn
        self.accent_rgb: tuple[float, float, float] | None = None  # the colour of the song: it fades between songs
        self.buffer = ""  # what is being typed in the chat bar
        self.composing = False  # Enter opened an empty line, so M and Space type instead of acting
        self.hotkeys = Hotkeys()  # the keys for Mute and Listen (from the settings)
        self.mouse = True  # the buttons can be clicked
        self.buttons: dict[str, tuple[int, int]] = {}  # name -> columns on the chat bar's top edge, set by the last frame
        self.buttons_row = 0  # that edge's row (0 is the top of the screen)
        self.said, self.said_at = "", 0.0  # the latest thing Minty said, and when it started being said

    def apply(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "state":
            self.state = event["state"]
        elif kind == "level":
            self.db, self.speech = event.get("db", -90.0), bool(event.get("speech"))
            self.threshold_db = event.get("threshold_db", self.threshold_db)
        elif kind == "partial":
            self.caption = event.get("text", "")
            self.tentative = event.get("tentative", "")
            self.typing.set(self.caption, self.tentative)
        elif kind == "transcript":
            self.caption = "" if event.get("role") == "user" else self.caption
            if event.get("role") == "user":
                self.tentative = ""  # what you said is final now: the live words in the bar go
                self.typing.set("", "")
            self.turns.append((event.get("role", ""), event.get("text", "")))
            if event.get("role") == "assistant":
                self.said, self.said_at = str(event.get("text", "")), time.monotonic()
            else:
                self.said = ""
        elif kind == "display":
            self.display_title, self.display_content = event.get("title", ""), event.get("content", "")
        elif kind == "notes":
            self.noting = bool(event.get("active"))
            self.notes = [str(n) for n in event.get("items", [])] if self.noting else []
        elif kind in ("error", "notice"):
            text = str(event.get("message", ""))[:140]
            if kind == "notice" and text.startswith("Self-check:") and text.rstrip().endswith("problems: none"):
                return  # a clean start-up check is not worth a line on a screen that is open all day
            self.notice, self.notice_at, self.notice_kind = text, time.monotonic(), kind

    @property
    def accent(self) -> str:
        r, g, b = self.accent_rgb or _hex_rgb(ACCENT)
        return f"#{round(r):02x}{round(g):02x}{round(b):02x}"

    def follow_accent(self, target: tuple[int, int, int] | None) -> None:
        """Move the accent colour towards the playing song's colour (the default ember when nothing sets one): a fade of
        about a second, or at once with reduced motion."""
        want = tuple(float(c) for c in (target or _hex_rgb(ACCENT)))
        if self.accent_rgb is None or self.reduced_motion:
            self.accent_rgb = want
        else:
            self.accent_rgb = tuple(a + (b - a) * 0.18 for a, b in zip(self.accent_rgb, want))

    def spoken_words(self, now: float | None = None) -> str:
        """What Minty is saying, a few words at a time as they are spoken, then the whole sentence for a while, then nothing."""
        if not self.said:
            return ""
        now = time.monotonic() if now is None else now
        words = self.said.split()
        elapsed = now - self.said_at
        if elapsed > len(words) / WORDS_PER_S + SAID_LINGER_S:
            return ""
        return " ".join(words[:min(len(words), int(elapsed * WORDS_PER_S) + 1)])

    def active_notice(self, now: float | None = None) -> str:
        """The latest warning or error while it is fresh, else empty."""
        now = time.monotonic() if now is None else now
        return self.notice if self.notice and now - self.notice_at <= NOTICE_FOR_S else ""


def caption_line(model: TuiModel, now: float) -> Text:
    shown, tentative = model.typing.step(now)
    out = Text(style="italic")
    out.append(shown, style="italic")
    if tentative:
        out.append((" " if shown else "") + tentative, style="italic dim")
    if shown or tentative:
        out.append(" …")
    return out


def _pct(db: float) -> float:
    return max(0.0, min(1.0, (db + 60.0) / 60.0))


def wave(model: TuiModel, width: int, now: float) -> Text:
    """A pulsing waveform: follows your voice while listening, breathes while Minty speaks."""
    color = COLORS.get(model.state, "white")
    target = _pct(model.db)
    model.level += (target - model.level) * (0.6 if target > model.level else 0.12)
    if model.state in ("speaking", "live"):
        amp = 0.55 + 0.3 * math.sin(now * 3.1) * math.sin(now * 1.3)
    elif model.state in ("thinking", "processing", "executing"):
        amp = 0.18
    elif model.state == "listening":
        amp = max(0.08, model.level)
    else:
        amp = 0.05 + 0.1 * model.level
    out = Text()
    for i in range(width):
        envelope = math.sin(math.pi * (i + 0.5) / width) ** 0.8  # tall in the middle, like a voice orb
        if model.state == "thinking":
            h = 0.5 + 0.5 * math.sin(now * 6 - i * 0.55)
        else:
            h = abs(math.sin(now * 5.0 + i * 0.45) * 0.6 + math.sin(now * 2.3 - i * 0.2) * 0.4)
        out.append(BARS[min(8, int(amp * envelope * h * 9 * 1.4))], style=color)
    return out


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# (centre, edge) colour of the orb in each state
ORB_COLORS = {"idle": ((150, 150, 150), (55, 55, 55)), "listening": ((140, 250, 255), (10, 100, 140)),
              "thinking": ((255, 228, 120), (170, 80, 20)), "speaking": ((160, 255, 160), (20, 120, 60)),
              "live": ((255, 160, 245), (110, 30, 160)), "loading": ((140, 180, 255), (30, 60, 140)),
              "processing": ((170, 215, 255), (40, 80, 130)), "executing": ((255, 200, 120), (150, 80, 20)),
              "error": ((255, 120, 120), (130, 30, 30))}
SUPERSAMPLE = 2  # samples per axis inside each pixel: anti-aliases the glow


def _g(x: float, s: float) -> float:
    return math.exp(-(x / s) ** 2)


def _comets(t: float) -> list[tuple[float, float, float, float]]:
    """(x, y, size, weight) of three comets and their tails orbiting at time t."""
    out = []
    for k in range(3):
        for j in range(6):
            ang = t * 2.4 + k * 2 * math.pi / 3 - j * 0.16
            out.append((0.7 * math.cos(ang), 0.7 * math.sin(ang), 0.2 - j * 0.015, 1 - j / 6))
    return out


def _field(state: str, x: float, y: float, t: float, level: float, comets=None) -> float:
    """Brightness 0..1 of the orb at (x, y) in -1..1."""
    r, a = math.hypot(x, y), math.atan2(y, x)
    if state == "thinking":  # three comets orbiting a small core
        v = _g(r, 0.24) * 0.95 + _g(r - 0.7, 0.06) * 0.3
        for cx, cy, size, weight in comets if comets is not None else _comets(t):
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if d2 < 0.25:  # beyond 0.5 a comet adds nothing visible
                v += math.exp(-d2 / (size * size)) * weight
        return v
    if state in ("speaking", "live"):  # a pulsing core sending out ripples
        amp = 0.6 + 0.4 * abs(math.sin(t * 3.1) * math.sin(t * 1.3))
        r += 0.025 * amp * math.sin(6 * a + t * 4)
        v = _g(r, 0.18 + 0.14 * amp)
        for k in range(3):
            rr = 0.3 + ((t * 0.55 + k / 3) % 1.0) * 0.68
            v += _g(r - rr, 0.09) * (1.1 - 0.55 * rr)
        return v
    if state == "listening":  # the core grows with your voice
        core = 0.22 + 0.55 * level
        return 1 / (1 + math.exp((r - core) / 0.07)) + _g(r - 0.9, 0.07) * 0.6
    if state in ("loading", "executing"):  # an arc going round
        return _g(r - 0.68, 0.11) * ((math.cos(a - t * 3.0) + 1) / 2) ** 2
    if state == "processing":  # calm: a softly pulsing core, nothing racing (that is Thinking mode's look)
        return _g(r, 0.26 + 0.04 * math.sin(t * 2.2)) * 0.95 + _g(r - 0.78, 0.07) * 0.35
    if state == "error":
        return _g(r, 0.3) * 0.9
    # idle: a slowly breathing core inside a still ring (slow enough to look smooth at a low redraw rate)
    return _g(r - 0.78, 0.08) * 0.55 + _g(r, 0.2 + 0.06 * math.sin(t * 1.2)) * 0.9


def orb(model: TuiModel, now: float, cols: int = 12, rows: int = 6) -> Text:
    """A small animated orb: every cell is two stacked pixels (▀ ▄) whose brightness follows a smooth glow."""
    state = model.state if model.state in ORB_COLORS else "idle"
    inner, outer = ORB_COLORS[state]
    if state == "idle" and model.accent_rgb:  # waiting: the orb glows in the colour of the song (active states keep theirs)
        inner, outer = (tuple(int(c * 0.62) for c in model.accent_rgb), tuple(int(c * 0.18) for c in model.accent_rgb))
    comets = _comets(now) if state == "thinking" else None
    size, ss = rows * 2, SUPERSAMPLE

    def pixel(px: int, py: int) -> tuple[int, int, int] | None:
        total = 0.0
        for sy in range(ss):
            for sx in range(ss):
                x = (px + (sx + 0.5) / ss) / cols * 2 - 1
                y = (py + (sy + 0.5) / ss) / size * 2 - 1
                total += _field(state, x, y, now, model.level, comets)
        v = total / (ss * ss)
        if v < 0.05:
            return None
        r = min(1.0, math.hypot((px + 0.5) / cols * 2 - 1, (py + 0.5) / size * 2 - 1))
        mix = max(0.0, min(1.0, 0.5 * (1 - r) + 0.5 * v))
        light = min(1.0, v) ** 0.75
        return tuple(int((o + (i - o) * mix) * light) for i, o in zip(inner, outer))

    out = Text()
    for row in range(rows):
        for col in range(cols):
            top, bottom = pixel(col, row * 2), pixel(col, row * 2 + 1)
            if top is None and bottom is None:
                out.append(" ")
            elif bottom is None:
                out.append("\u2580", style=f"rgb({top[0]},{top[1]},{top[2]})")
            elif top is None:
                out.append("\u2584", style=f"rgb({bottom[0]},{bottom[1]},{bottom[2]})")
            else:
                out.append("\u2580", style=f"rgb({top[0]},{top[1]},{top[2]}) on rgb({bottom[0]},{bottom[1]},{bottom[2]})")
        if row < rows - 1:
            out.append("\n")
    return out


def meter(model: TuiModel, width: int) -> Text:
    filled, mark = int(_pct(model.db) * width), min(width - 1, int(_pct(model.threshold_db) * width))
    out = Text("mic ")
    for i in range(width):
        if i == mark:
            out.append("│", style="red")
        elif i < filled:
            out.append("█", style="green" if model.speech else "grey50")
        else:
            out.append("░", style="grey30")
    out.append(f" {model.db:5.0f} dB", style="bold" if model.speech else "dim")
    out.append("  voice" if model.speech else "  quiet", style="green" if model.speech else "dim")
    return out


def _bar(share: float, cells: int = 8) -> str:
    filled = max(0, min(cells, round(share * cells)))
    return "\u25b0" * filled + "\u25b1" * (cells - filled)


def _capacity_text(cap: dict) -> str:
    return f"{_bar(cap['share'])} {round(cap['share'] * 100)}%"


def _capacity_detail(cap: dict) -> str:
    left = f"~{cap['calls_left']} calls left" if cap.get("calls_left") is not None else ""
    when = str(cap.get("forecast") or "").replace("lasts until ", "until ")
    both = " \u00b7 ".join(x for x in (left, when) if x)
    if len(both) <= 22:
        return both
    return (when or left) if cap.get("share", 1) < 0.15 else (left or when)  # one line: running low, say when


def status_items(st: dict) -> list[tuple[str, str, str]]:
    """(label, value, style) for each indicator, in display order. A row with an empty label continues the one above."""
    if not st:
        return []
    live = st.get("voice_mode") == "live"
    deep = st.get("tier") == "deep"
    work = _work(st)
    ready, total = st.get("brain_ready", 0), st.get("brain_total", 0)
    cooling = st.get("cooling") or []
    issues = [f"{cooling[0]} resting"] if cooling else []
    issues += [f"{r['name']} {r['state']}" for r in st.get("health") or []
               if r.get("state") in ("demoted", "key rejected", "unreachable")]
    brain = f"{ready}/{total} ready" + (f"  ({', '.join(issues)})" if issues else "")
    picked = st.get("picked") or ""
    if live:
        model = "Gemini Live"
    else:
        model = st.get("model") or picked or "auto"
        if picked:
            model += " \u2605" if model.startswith(picked) else f"  ({picked} unavailable)"  # \u2605: your pick
    cap = st.get("capacity") or {}
    return [
        ("MODEL", model, "bold white"),
        ("THINKING", "DEEP" if deep else "fast", "bold yellow" if deep else "grey62"),
        ("BRAIN", brain, "yellow" if ready < total else "grey62"),
        *([("CAPACITY", _capacity_text(cap), "bold yellow" if cap["share"] < 0.15 else "grey62")]
          if cap.get("share") is not None else []),
        *([("", _capacity_detail(cap), "grey42")] if cap.get("share") is not None else []),
        ("VOICE", "LIVE  (Gemini)" if live else "CLASSIC  (local)", "bold magenta" if live else "bold cyan"),
        ("SPEECH", f"{st.get('tts', '?')} \u00b7 wake: {st.get('wake', '?')}", "grey62"),
        ("MIC", "MUTED" if st.get("muted") else "on" + (f" \u00b7 ignored noise \u00d7{st['noise_ignored']}"
                                                         if st.get("noise_ignored") else ""),
         "bold red" if st.get("muted") else "green"),
        ("MUSIC", "\u266a playing" if st.get("music") else "none", "bold green" if st.get("music") else "grey62"),
        *([("PRIVACY MODE", "ON \u00b7 nothing leaves except to approved providers", "bold red")]
          if st.get("privacy_banner") else []),
        ("PRIVATE", "ON" if st.get("private") else "off", "bold red" if st.get("private") else "grey62"),
        ("WORKING", _work_summary(work), "bold blue" if work else "grey62"),
        ("MEMORY", f"{st.get('context_msgs', 0)} msgs \u00b7 {st.get('rules', 0)} learned", "grey62"),
    ]


def status_strip(st: dict, accent: str = ACCENT) -> Text:
    """One quiet line: the model, the voice mode and the microphone, and only what needs attention besides (muted, private, deep
    thinking, models resting, running low, work in the background). Empty when there is no status."""
    if not st:
        return Text()
    live = st.get("voice_mode") == "live"
    name = "Gemini Live" if live else (st.get("model") or st.get("picked") or "auto")
    bits: list[tuple[str, str]] = [(name, f"bold {accent}"), ("live" if live else "classic", "grey62"),
                                   ("MUTED" if st.get("muted") else "mic on", "bold red" if st.get("muted") else "green")]
    ready, total = st.get("brain_ready", 0), st.get("brain_total", 0)
    if total and ready < total:
        cooling = st.get("cooling") or []
        bits.append((f"{ready}/{total} models ready" + (f" ({cooling[0]} resting)" if cooling else ""), "yellow"))
    cap = st.get("capacity") or {}
    if cap.get("share") is not None and cap["share"] < 0.25:
        bits.append((f"{round(cap['share'] * 100)}% left" + (f" \u00b7 {cap['forecast']}" if cap["share"] < 0.15
                                                              and cap.get("forecast") else ""), "bold yellow"))
    if st.get("tier") == "deep":
        bits.append(("deep thinking", "yellow"))
    if st.get("private"):
        bits.append(("private", "bold red"))
    if st.get("privacy_banner"):
        bits.append(("privacy mode", "bold red"))
    work = _work(st)
    if work:
        bits.append((_work_summary(work), "bold blue"))
    out = Text(no_wrap=True, overflow="ellipsis")
    for n, (text, style) in enumerate(bits):
        if n:
            out.append("  \u00b7  ", style="grey42")
        out.append(text, style=style)
    return out


KIND_NAMES = {"browser": ("browser", "browser"), "subagent": ("helper", "helpers"),
              "coding": ("coding job", "coding jobs"), "think": ("deep think", "deep thinks"),
              "research": ("research", "research")}


def _work(st: dict) -> list[dict]:
    if "work" in st:
        return st["work"]
    return ([{**j, "kind": "subagent"} for j in st.get("subagents", [])]
            + [{**j, "kind": "coding"} for j in st.get("jobs", [])])


def _work_summary(work: list[dict]) -> str:
    if not work:
        return "nothing in the background"
    counts: dict[str, int] = {}
    for w in work:
        counts[w["kind"]] = counts.get(w["kind"], 0) + 1
    parts = []
    for kind, n in counts.items():
        one, many = KIND_NAMES.get(kind, (kind, kind))
        parts.append(f"{n} {one if n == 1 else many}")
    waiting = sum(1 for w in work if w.get("status") == "needs_user")
    return " \u00b7 ".join(parts) + (f" \u00b7 {waiting} need you" if waiting else "")


def working(st: dict) -> Text | None:
    rows = _work(st)
    if not rows:
        return None
    out = Text()
    for w in rows[:6]:
        status = w.get("status", "running")
        mark, style = {"needs_user": ("?", "bold yellow"), "queued": ("…", "grey50")}.get(status, ("●", "blue"))
        out.append(f"{mark} {KIND_NAMES.get(w['kind'], (w['kind'],))[0]} #{w['id']}  ", style=style)
        out.append(f"{w['what'][:60]}  ", style="white")
        out.append(f"{w['secs'] // 60}m{w['secs'] % 60:02d}s", style="dim")
        detail = w.get("question") if status == "needs_user" else w.get("progress")
        if detail:
            out.append(f"  — {detail[:50]}", style="yellow" if status == "needs_user" else "grey62")
        out.append("\n")
    return out



FONT = {  # the shapes, 3 pixels wide and 5 tall; digit_pixels() draws each one three times finer
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "001", "010", "010"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
}
SCALE = 3  # each shape pixel is 3 x 3 clock pixels
DIGIT_W, DIGIT_H = 3 * SCALE, 5 * SCALE
CLOCK_ROWS = (DIGIT_H + 1) // 2  # a text row holds two pixels (half blocks), which are square on screen
COLON_W = 2  # two pixels square, on a text-row boundary so it is two clean blocks
CLOCK_BLOCK_W = 4 * DIGIT_W + COLON_W + 4  # four digits and the colon, one cell between each
MARK_W = 3  # a cell, then AM or PM
CLOCK_W = CLOCK_BLOCK_W + MARK_W


def digit_pixels(ch: str) -> list[str]:
    """A digit as DIGIT_H rows of "0"/"1": the 3 x 5 shape enlarged three times, with each outer corner cut by one pixel
    so the edges step like pixel art instead of ending in sharp squares."""
    big = [[px == "1" for px in row for _ in range(SCALE)] for row in FONT[ch] for _ in range(SCALE)]

    def on(y: int, x: int) -> bool:
        return 0 <= y < DIGIT_H and 0 <= x < DIGIT_W and big[y][x]

    out = []
    for y in range(DIGIT_H):
        row = ""
        for x in range(DIGIT_W):
            keep = big[y][x]
            if keep:
                for dy, dx in ((-1, -1), (-1, 1), (1, -1), (1, 1)):  # a pixel with nothing above/below, beside or across it
                    if not on(y + dy, x) and not on(y, x + dx) and not on(y + dy, x + dx):
                        keep = False
            row += "1" if keep else "0"
        out.append(row)
    return out


def _blocks(pixels: list[str]) -> list[str]:
    """Pixel rows to text rows: two pixels per cell as space, upper half, lower half or full block."""
    rows = []
    for top in range(0, len(pixels), 2):
        upper = pixels[top]
        lower = pixels[top + 1] if top + 1 < len(pixels) else "0" * len(upper)
        rows.append("".join({"00": " ", "10": "\u2580", "01": "\u2584", "11": "\u2588"}[u + d] for u, d in zip(upper, lower)))
    return rows


def clock_block(wall: datetime, colon: bool = True) -> list[str]:
    """H:MM (12-hour, or HH:MM with the clock_24h setting) as CLOCK_ROWS rows of half blocks, all the same width; the colon is two squares (blank when `colon`
    is False). A one-digit hour leaves its first digit blank so nothing moves when the hour changes."""
    h, m = (f"{wall.hour:02d}" if is_24h() else f"{wall.hour % 12 or 12:2d}"), f"{wall:%M}"
    dot = ["1" * COLON_W if colon and y in (4, 5, 10, 11) else "0" * COLON_W for y in range(DIGIT_H)]
    digits = [digit_pixels(c) if c != " " else ["0" * DIGIT_W] * DIGIT_H for c in h + m]
    pixels = ["".join((digits[0][y], "0", digits[1][y], "0", dot[y], "0", digits[2][y], "0", digits[3][y]))
              for y in range(DIGIT_H)]
    return _blocks(pixels)


def clock_text(wall: datetime, rgb: tuple[float, float, float], blink: bool, reduced: bool) -> Text:
    """The clock for the header: solid digits shaded from the song's colour at the top to a deeper tone at the bottom, a colon
    that blinks each second, and the day and date in small spaced capitals."""
    out = Text(justify="center")
    mark = "" if is_24h() else "AM" if wall.hour < 12 else "PM"
    for n, row in enumerate(clock_block(wall, colon=reduced or blink)):
        shade = _shade(rgb, 1.0 - 0.4 * n / (CLOCK_ROWS - 1))
        out.append(row, style=f"bold {shade}")
        out.append((" " + mark) if mark and n == CLOCK_ROWS - 2 else " " * MARK_W, style=f"bold {shade}")  # beside the bottom of the digits
        out.append("\n")
    out.append(" ".join(f"{wall:%a}".upper()) + "  \u00b7  " + " ".join(f"{wall:%d}") + "  \u00b7  " + " ".join(f"{wall:%b}".upper()),
               style="grey58")
    return out


def title_text() -> Text:
    """Minty v1.0.0-alpha.3 with the release stage (alpha, beta) in red: it is not finished and says so."""
    base, _, stage = __version__.partition("-")
    t = Text()  # no base style: a Panel title keeps spans' colours only when the whole text has none of its own
    t.append("Minty ", style="bold white")  # explicit colours: the border may be a dim tone of the song's colour
    t.append(f"v{base}", style="bold white")
    if stage:
        t.append("-", style="bold white")
        t.append(stage, style=f"bold {ALPHA_RED}")
    return t


def _mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


PLAY_ICON = ("\u2588\u2584  ", "\u2588\u2588\u2588\u2588", "\u2588\u2580  ")  # a right-pointing triangle, three rows
PAUSE_ICON = ("\u2588\u2588 \u2588\u2588", "\u2588\u2588 \u2588\u2588", "\u2588\u2588 \u2588\u2588")


def equaliser(now: float, bars: int, reduced: bool) -> list[str]:
    """Three rows of moving bars (each two cells wide) for the card; flat and low with reduced motion."""
    heights = [3 if reduced else 3 + int(21 * (0.5 + 0.5 * math.sin(now * 5.0 + i * 1.1) * math.sin(now * 1.7 + i * 0.5)))
               for i in range(bars)]
    rows = []
    for r in range(3):
        rows.append("".join(BARS[max(0, min(8, h - (2 - r) * 8))] * 2 for h in heights))
    return rows


def handle_key(model: TuiModel, key: str) -> str | None:
    """One key for the chat bar. Returns the line to send when Enter is pressed on something typed."""
    if key in ("\r", "\n"):
        text, model.buffer = model.buffer.strip(), ""
        return text or None
    if key in ("\x7f", "\x08"):
        model.buffer = model.buffer[:-1]
    elif key == "\x15":  # ctrl-u
        model.buffer = ""
    elif len(key) == 1 and key.isprintable() and len(model.buffer) < MAX_TYPED:
        model.buffer += key
    return None


@dataclass(frozen=True)
class Hotkeys:
    """The two keys that work while nothing is typed: mute and start listening ("space" is the space bar)."""
    mute: str = "m"
    listen: str = " "
    enabled: bool = True

    @classmethod
    def from_config(cls, cfg) -> "Hotkeys":
        key = lambda name: " " if name == "space" else name  # noqa: E731
        return cls(key(cfg.terminal_mute_key), key(cfg.terminal_listen_key), bool(cfg.terminal_hotkeys))

    @staticmethod
    def name(key: str) -> str:
        return "Space" if key == " " else key.upper()


class Mouse(NamedTuple):
    button: int  # 0 left, 1 middle, 2 right, 64/65 the wheel
    x: int  # columns and rows count from 1, as the terminal reports them
    y: int
    press: bool


_MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")


def dispatch_key(model: TuiModel, key: str, keys: Hotkeys | None = None) -> tuple[str, str | None] | None:
    """One key for the display: ("mute", None), ("listen", None) or ("send", line), else None (the key edited the line).
    With nothing typed the mute and listen keys act; Enter opens an empty line (so a message can start with M or a space)
    and Enter or Backspace on that empty line closes it again."""
    keys = keys or model.hotkeys
    if not model.buffer and not model.composing and keys.enabled:
        if key == keys.mute:
            return ("mute", None)
        if key == keys.listen:
            return ("listen", None)
    if not model.buffer and key in ("\r", "\n"):
        model.composing = keys.enabled and not model.composing
        return None
    if not model.buffer and key in ("\x7f", "\x08"):
        model.composing = False
        return None
    line = handle_key(model, key)
    if line is not None:
        model.composing = False
        return ("send", line)
    return None


def click_action(model: TuiModel, event: Mouse) -> str | None:
    """"mute" or "listen" when the left button was pressed on that button, else None."""
    if not model.mouse or event.button != 0 or not event.press or event.y != model.buttons_row + 1:
        return None
    for name, (left, right) in model.buttons.items():
        if left < event.x <= right:  # the terminal counts columns from 1
            return name
    return None


def parse_keys(data: str) -> list:
    """Split what arrived on stdin into keys and mouse reports, dropping other escape sequences (arrows, function keys)."""
    keys, i = [], 0
    while i < len(data):
        ch = data[i]
        if ch == "\x1b":
            m = _MOUSE.match(data, i)
            if m:
                keys.append(Mouse(int(m[1]), int(m[2]), int(m[3]), m[4] == "M"))
                i = m.end()
                continue
            i += 1
            if i < len(data) and data[i] in "[O":
                i += 1
                while i < len(data) and not ("@" <= data[i] <= "~"):
                    i += 1
            i += 1
            continue
        keys.append(ch)
        i += 1
    return keys


def chat_bar(model: TuiModel, width: int, now: float, accent: str, rgb, muted: bool = False) -> Panel:
    """The bar at the bottom: what you type, the words as they are heard while you speak, the words as Minty speaks them,
    or a hint. It is always three rows tall."""
    inner = max(10, width - 6)
    cursor = "\u258c" if int(now * 2) % 2 == 0 else " "
    text = Text(no_wrap=True, overflow="ellipsis")
    typing = bool(model.buffer)
    words = model.spoken_words()
    shown, tentative = (model.caption, model.tentative) if model.state in ("listening", "live") else ("", "")
    if typing:
        body = model.buffer
        text.append("\u203a ", style=f"bold {accent}")
        text.append(body[-(inner - 4):] if len(body) > inner - 4 else body, style="white")
        text.append(cursor, style=accent)
    elif shown or tentative:
        live = (shown + (tentative if tentative.startswith(" ") or not shown else " " + tentative)).strip()
        text.append("you \u203a ", style="bold cyan")
        text.append(live[-(inner - 8):] if len(live) > inner - 8 else live, style="white")
        text.append(cursor, style="cyan")
    elif words:
        text.append("Minty \u203a ", style=f"bold {accent}")
        text.append(words[-(inner - 10):] if len(words) > inner - 10 else words, style="grey85")
    elif model.composing:
        text.append("\u203a ", style=f"bold {accent}")
        text.append(cursor, style=accent)
    else:
        text.append("\u203a ", style=f"bold {accent}")
        hint = "Say \u201cMinty\u201d"
        if muted:
            hint = "Muted: the microphone is off" + (f". Press {Hotkeys.name(model.hotkeys.mute)} to unmute" if model.hotkeys.enabled else "")
        elif model.hotkeys.enabled:
            hint += f", press {Hotkeys.name(model.hotkeys.listen)}, or press Enter to type"
        else:
            hint += ", or type here and press Enter"
        text.append(hint, style=("red" if muted else "grey42") + " italic")
    edge = accent if typing else _shade(rgb, 0.5) if rgb else "grey35"
    title, labels = button_title(model, accent, muted)
    panel = Panel(text, box=box.ROUNDED, border_style=edge, height=CHAT_ROWS, padding=(0, 1), title=title, title_align="right")
    model.buttons = _locate(panel, width, labels) if labels else {}
    return panel


def button_title(model: TuiModel, accent: str, muted: bool) -> tuple[Text | None, dict[str, str]]:
    """The two buttons as the chat bar's title, and the exact text of each (to find where they landed)."""
    if not (model.hotkeys.enabled or model.mouse):
        return None, {}
    key = lambda k: Hotkeys.name(k) + " " if model.hotkeys.enabled else ""  # noqa: E731
    mute = f" {key(model.hotkeys.mute)}{'Muted' if muted else 'Mute'} "
    listen = f" {key(model.hotkeys.listen)}{'Listening' if model.state == 'listening' else 'Listen'} "
    title = Text()
    title.append(mute, style="bold white on #e5484d" if muted else "grey70 on grey19")
    title.append(" ")
    title.append(listen, style=f"bold black on {accent}" if model.state == "listening" else "grey70 on grey19")
    return title, {"mute": mute, "listen": listen}


def _locate(panel: Panel, width: int, labels: dict[str, str]) -> dict[str, tuple[int, int]]:
    """Columns of each label on the panel's top edge, found by drawing it once."""
    console = Console(width=width, file=io.StringIO(), color_system=None, force_terminal=False)
    console.print(panel)
    first = console.file.getvalue().split("\n")[0]
    found = {}
    for name, label in labels.items():
        at = first.find(label)
        if at >= 0:
            found[name] = (at, at + len(label))
    return found


def _measure_rows(renderable, width: int) -> int:
    console = Console(file=io.StringIO(), width=max(10, width), color_system=None, force_terminal=False)
    console.print(renderable)
    return len(console.file.getvalue().splitlines())


MIN_MIDDLE = 24  # the "coming up" column is only worth showing at least this wide


def column_widths(width: int, card_w: int) -> tuple[int, int, int]:
    """Widths of the conversation panel's columns: the chat (about half of the panel), the "coming up" list in the free space,
    and the card's column (the card plus one column of padding either side). The middle column is dropped when it would be
    narrower than MIN_MIDDLE; the chat then takes its width."""
    content = width - 4
    card_col = card_w + 2 if card_w else 0
    chat = int(content * 0.5)
    middle = content - chat - card_col
    if middle < MIN_MIDDLE:
        return content - card_col, 0, card_col
    return chat, middle, card_col


def _when(due: float, remaining: float, kind: str, wall: datetime) -> str:
    if kind == "timer":
        return f"\u23f1 {_mmss(remaining)}"
    at = datetime.fromtimestamp(due)
    if at.date() == wall.date():
        return time_text(at)
    if (at.date() - wall.date()).days == 1:
        return f"tmrw {time_text(at)}"
    return f"{at:%a} {time_text(at)}"


def coming_up_block(st: dict, wall: datetime, accent: str, lines: int, width: int) -> Group:
    """The free space of the conversation panel: your reminders and running timers, soonest first."""
    items = st.get("agenda") or []
    rows = [Rule(Text("COMING UP", style=f"bold {accent}"), align="left", style="grey27", characters="\u2500")]
    if not items:
        rows.append(Text("Nothing coming up", style="grey42 italic"))
        rows.append(Text("say \u201cremind me at 6 to \u2026\u201d", style="grey35"))
        return Group(*rows)
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True, min_width=9, style=_shade(_hex_rgb(accent), 0.8))
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    for it in items[:max(1, lines - 2)]:
        grid.add_row(_when(it["due"], it["remaining_s"], it["kind"], wall), Text(it["text"], style="white"))
    rows.append(grid)
    return Group(*rows)


def card_plan(setting_cols: int, available: int, gfx: bool) -> tuple[int, int] | None:
    """(cover columns, cover rows) for the card, from the rows it may use: the cover is square (twice as many columns as rows),
    as big as the setting asks and as the space allows; (0, 0) is a card without a cover; None is no card at all."""
    most = max(setting_cols if gfx else COVER_COLS, 8) // 2
    rows = min(most, available - CARD_TEXT_ROWS - CARD_BORDER_ROWS)
    if rows >= 4:
        return rows * 2, rows
    return (0, 0) if available >= CARD_TEXT_ROWS + CARD_BORDER_ROWS else None


def music_card(st: dict, now: float, reduced: bool, cover: Text | None = None, cover_cols: int = COVER_COLS,
               accent: str = ACCENT, rgb: tuple[float, float, float] | None = None) -> Panel:
    """What is playing, as a tall card: the album cover, then title, artist, progress and an equaliser, in the song's colour."""
    track, playing = st.get("now_playing"), bool(st.get("music"))
    inner = max(cover_cols, CARD_MIN_INNER) if cover is not None else TEXT_ONLY_W - 4
    width = inner + 4
    edge = _shade(rgb, 0.5) if rgb else "grey35"
    head = Text("\u266a NOW PLAYING", style=f"bold {accent}", no_wrap=True, overflow="ellipsis")
    if not track:
        body = Text()
        body.append("Playing\n" if playing else "Nothing playing", style="bold green" if playing else "grey42 italic")
        if playing:
            body.append("no track details", style="grey42")
        return Panel(Group(head, body), box=box.ROUNDED, border_style=edge, width=width, padding=(0, 1))
    parts: list = [head]
    if cover is not None:
        parts.append(Padding(cover, (0, 0, 0, (inner - cover_cols) // 2)))  # centred when the cover is narrower than the text
    if cover is not None:
        parts.append(Text(" "))  # one row of air between the picture and the title
    title_lines = textwrap.wrap(track["title"] or "Unknown", inner)
    shown = title_lines[:2] or [""]
    if len(title_lines) > 2:
        shown[-1] = shown[-1][:inner - 1] + "\u2026"
    parts.append(Text("\n".join(shown), style="bold white"))  # one row, or two when it wraps: the artist is right under it
    artist, album = track["artist"], track["album"]
    sub = f"{artist} \u00b7 {album}" if artist and album and len(artist) + len(album) + 3 <= inner else (artist or album or " ")
    parts.append(Text(sub if len(sub) <= inner else sub[:inner - 1] + "\u2026", style="grey62", no_wrap=True))
    length, pos = track["length_s"], track["position_s"]
    left_t, right_t = _mmss(pos), _mmss(length)
    room = inner - len(left_t) - len(right_t) - 2
    line = Text(no_wrap=True)
    if length and room >= 4:
        at = max(0, min(room - 1, int(pos / length * (room - 1))))
        line.append(left_t + " ", style="grey50")
        line.append("\u2501" * at, style=accent)
        line.append("\u25cf", style="bold white")
        line.append("\u2500" * (room - at - 1), style="grey30")
        line.append(" " + right_t, style="grey50")
    else:
        line.append(f"{left_t} / {right_t}" if length else "live", style="grey42")
    parts.append(line)
    if track["status"] != "Playing":
        for r, row in enumerate(PAUSE_ICON):
            line = Text(no_wrap=True)
            line.append(row, style="bold yellow")
            if r == 1:
                line.append("  paused", style="yellow")
            parts.append(line)
    else:
        bars = max(1, (inner - len(PLAY_ICON[1]) - 1) // 2)  # the bars take what the icon leaves
        moving = equaliser(now, bars, reduced)
        for r in range(3):
            line = Text(no_wrap=True)
            line.append(PLAY_ICON[r], style=f"bold {accent}")
            line.append(" " + moving[r], style=accent)
            parts.append(line)
    return Panel(Group(*parts), box=box.ROUNDED, border_style=edge, width=width, padding=(0, 1))


def conversation(model: TuiModel, lines: int, text_width: int):
    """The latest turns (or, while noting, the latest notes) that fit in `lines` lines, newest at the bottom."""
    accent = model.accent
    rows: list[tuple[Text, Text, int]] = []
    if model.noting:
        for note in model.notes[-8:]:
            rows.append((Text("\u2022", style=f"bold {accent}", justify="right"), Text(note),
                         -(-len(note) // max(10, text_width))))
    else:
        for role, text in list(model.turns)[-8:]:
            who = Text("You" if role == "user" else "Minty", style="bold cyan" if role == "user" else f"bold {accent}",
                       justify="right")
            rows.append((who, Text(text[:400], style="white" if role == "user" else "grey85"),
                         -(-len(text[:400]) // max(10, text_width))))
    kept, used = [], 0
    for who, text, height in reversed(rows):
        if used + height > lines and kept:
            break
        kept.append((who, text))
        used += height
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(width=6, no_wrap=True)
    grid.add_column(ratio=1)
    for who, text in reversed(kept):
        grid.add_row(who, text)
    if model.noting:
        grid.add_row("", Text("\nsay \u201cMinty, stop noting\u201d to finish", style="grey42 italic"))
    return grid


def render(model: TuiModel, width: int = 80, height: int = 30, now: float | None = None,
           wall: datetime | None = None):
    now = time.monotonic() if now is None else now
    if model.reduced_motion:  # shapes and colours still show the state; nothing moves except the live meter
        now = 0.0
    wall = wall or datetime.now()
    color = COLORS.get(model.state, "white")
    flags = model.flags()
    badges = "  ".join(f"[{name}]" for name, on in flags.items() if on)
    st = model.status()
    track = st.get("now_playing") if st else None
    muted = bool(st.get("muted")) if st else False
    model.follow_accent(MUTED_RGB if muted else (model.covers.accent(track) if track else None))  # red when muted, else the song
    accent = model.accent
    label = LABELS.get(model.state, model.state.upper())
    if muted:  # the one thing that matters while muted: no "waiting for", no "listening", no spinner
        color, label = ALPHA_RED, "MUTED"
    head = Text()
    head.append("\u25cf ", style=color)
    head.append(label, style=f"bold {color}")
    if muted:
        pass
    elif model.state in ("thinking", "loading", "executing") and not model.reduced_motion:
        head.append(" " + SPINNER[int(now * 12) % len(SPINNER)], style=color)
    elif model.state in ("speaking", "live") and not model.reduced_motion:
        head.append(" " + "".join(BARS[1 + int(3.5 * (1 + math.sin(now * 7 + i * 1.7)))] for i in range(3)),
                    style=color)
    if badges:
        head.append("   " + badges, style="dim")
    show_orb, show_clock = width >= 60, width >= CLOCK_MIN_WIDTH
    side = max(10, width - 6 - (14 if show_orb else 0) - (CLOCK_W + 2 if show_clock else 0))
    lines = [head, wave(model, side, now), meter(model, max(10, min(40, side - 28)))]
    notice = model.active_notice()
    if notice:
        lines.append(Text(("! " if model.notice_kind == "error" else "\u26a0 ") + notice,
                          style="red" if model.notice_kind == "error" else "yellow", no_wrap=True, overflow="ellipsis"))
    voice = Group(*lines)
    cols = [(orb(model, now), 12)] if show_orb else []
    cols.append((voice, None))
    if show_clock:
        cols.append((clock_text(wall, model.accent_rgb or _hex_rgb(ACCENT), wall.second % 2 == 0, model.reduced_motion),
                     CLOCK_W))
    face = Table.grid(padding=(0, 2, 0, 0), expand=True)
    for renderable, w in cols:
        face.add_column(no_wrap=False, **({"width": w} if w else {"ratio": 1}))
    face.add_row(*[c for c, _ in cols])
    layout = Layout()
    header = max(8, CLOCK_ROWS + 3) if show_clock else 8  # the clock, the date under it and the two border lines
    edge = _shade(model.accent_rgb, 0.45) if model.state == "idle" and model.accent_rgb else color  # waiting: the song's tone
    parts = [Layout(Panel(face, title=title_text(), border_style=edge, box=box.ROUNDED), name="voice", size=header)]
    used = header
    if st:
        parts.append(Layout(Padding(status_strip(st, accent), (0, 2)), name="strip", size=1))
        used += 1
        busy = working(st)
        if busy is not None:
            size = min(8, 2 + len(busy.plain.splitlines()))
            parts.append(Layout(Panel(busy, title="Working", border_style="blue", box=box.ROUNDED), name="working",
                                size=size))
            used += size
    if model.display_content:
        parts.append(Layout(Panel(Markdown(model.display_content), title=model.display_title or "Display",
                                  border_style="blue", box=box.ROUNDED), name="display", ratio=2))
    model.cover_at = None
    gfx = model.gfx if model.gfx is not None and not model.display_content else None  # a display pane moves the panel
    available = max(3, height - used - 2 - CHAT_ROWS) if not model.display_content else 6  # rows inside the conversation panel
    # one row of padding under the card, so it is planned for one row less
    plan = card_plan(model.cover_cols, available - 1, gfx is not None) if st else None
    inner_w = (max(plan[0], CARD_MIN_INNER) if plan[0] else TEXT_ONLY_W - 4) if plan else 0
    if plan is not None and width < inner_w + 4 + CONVO_MIN_WIDTH:
        plan = None  # too narrow: the card goes, the conversation stays
    card_w = (inner_w + 4) if plan else 0
    chat_w, middle_w, card_col = column_widths(width, card_w)
    talk = conversation(model, available, chat_w - 8)
    if not model.turns and not model.noting:
        talk = Text("Say \u201cMinty\u201d and ask anything.", style="dim italic")
    columns = [Layout(Padding(talk, (0, 1, 0, 0)), name="chat", **({"size": chat_w} if (middle_w or card_col) else {"ratio": 1}))]
    if middle_w and st:
        columns.append(Layout(Padding(coming_up_block(st, wall, accent, available, middle_w - 2), (0, 1, 0, 1)), name="coming",
                              ratio=1))
    if plan is not None:
        cover_cols, cover_rows = plan
        cover = None
        if track and cover_cols:
            if gfx is not None:
                side = min(cover_cols * gfx.cell_w, cover_rows * gfx.cell_h)  # square, whatever the cell shape
                picture = model.covers.picture(track, side, side, gfx.protocol)
                if picture is not None and used + 3 + cover_rows <= height:
                    cover = Text("\n".join(" " * cover_cols for _ in range(cover_rows)))  # the picture shows through
                else:  # still loading: a tile of the same size, so nothing moves when the picture arrives
                    picture = None
                    cover = tile(f"{track.get('artist', '')} {track.get('title', '')}", cover_cols, cover_rows)
            else:
                picture, cover = None, model.covers.get(track, cover_cols, cover_rows)
        card = music_card(st, now, model.reduced_motion, cover, cover_cols if cover is not None else 0, accent,
                          model.accent_rgb)
        card_h = _measure_rows(card, card_w)
        if track and cover is not None and gfx is not None and picture is not None:
            # the card sits at the bottom right: its top is `available - 1 - card_h` rows into the panel's content
            card_top = used + 1 + max(0, available - 1 - card_h)
            x = width - 2 - 1 - card_w + 2 + (inner_w - cover_cols) // 2  # panel border, one column of padding, card border
            model.cover_at = (picture, x, card_top + 2, cover_cols, cover_rows)
        columns.append(Layout(Align(Padding(card, (0, 1, 1, 1)), align="left", vertical="bottom"), name="card", size=card_col))
    body = Layout(name="convo-body")
    body.split_row(*columns) if len(columns) > 1 else None
    if len(columns) == 1:
        body = talk
    title = Text("\u270e Noting", style=f"bold {accent}") if model.noting else "Conversation"
    parts.append(Layout(Panel(body, title=title, border_style=accent if model.noting else "grey35", box=box.ROUNDED),
                        name="convo", ratio=1))
    parts.append(Layout(chat_bar(model, width, time.monotonic(), accent, model.accent_rgb, muted), name="chat", size=CHAT_ROWS))
    model.buttons_row = height - CHAT_ROWS  # the chat bar is the last thing on the screen: its top edge holds the buttons
    layout.split_column(*parts)
    return layout


ACTIVE_FPS, IDLE_FPS, STILL_FPS = 15.0, 6.0, 2.0


ACTIVE_SHARE, IDLE_SHARE = 0.35, 0.05  # most of one core the display may use while something moves / while waiting
ACTIVE_MAX_DELAY_S, IDLE_MAX_DELAY_S = 0.4, 2.0


def frame_delay(model: TuiModel, reduced_motion: bool = False, render_s: float = 0.0) -> float:
    """Seconds to the next redraw. Smooth while Minty listens, works or speaks or words are appearing; a gentle 6 per second
    while waiting (the meter still follows your voice); 2 per second with reduced motion. `render_s` is how long a frame took
    to draw: on a slow computer the redraws space themselves out so the display never takes more than a third of a core while
    something moves, or a twentieth while it waits (the look is the same, only the frame rate is lower; this PC is unaffected)."""
    busy = model.state != "idle" or model.typing.words or model.typing.tentative or model.buffer or model.spoken_words()
    if busy:
        base, share, cap = 1.0 / ACTIVE_FPS, ACTIVE_SHARE, ACTIVE_MAX_DELAY_S
    else:
        base, share, cap = 1.0 / (STILL_FPS if reduced_motion else IDLE_FPS), IDLE_SHARE, IDLE_MAX_DELAY_S
    return min(cap, max(base, render_s / share))


def choose_graphics(setting: str, fd: int, detect_fn=detect, cell_fn=cell_pixels) -> Graphics | None:
    """The terminal's picture protocol per the `terminal_images` setting ("auto" asks the terminal), with its cell size."""
    if setting == "off":
        return None
    protocol = setting if setting in ("sixel", "kitty") else detect_fn()
    return Graphics(protocol, *cell_fn(fd)) if protocol else None


def paint_images(model: TuiModel, overlay) -> None:
    """After a frame's text is on screen: put the real cover where the layout reserved it, or take it away."""
    if model.cover_at is None:
        overlay.show(None, 0, 0, 0, 0)
    else:
        overlay.show(*model.cover_at)


def safe_paint_images(model: TuiModel, overlay) -> bool:
    """`paint_images`, but any trouble (a terminal that lied about pictures, a closed pipe) turns pictures off for this
    run: the display goes on with the pixel-block cover and nothing is ever left half drawn. False when it did."""
    try:
        paint_images(model, overlay)
        return True
    except Exception:  # noqa: BLE001 - a picture must never take the display down
        log.warning("pictures failed; using the block cover from now on", exc_info=True)
        model.gfx, model.cover_at = None, None
        try:
            overlay.clear()
        except Exception:  # noqa: BLE001
            pass
        return False


class _KeyReader:
    """Reads typed keys and mouse clicks from the terminal: the terminal goes into cbreak mode (keys arrive one at a time,
    Ctrl+C still stops Minty), mouse reporting is switched on while the display is open, and both are put back on exit."""

    MOUSE_ON, MOUSE_OFF = "\x1b[?1000h\x1b[?1006h", "\x1b[?1000l\x1b[?1006l"

    def __init__(self, model: TuiModel, on_submit, wake: asyncio.Event | None, on_action=None, hotkeys=None, mouse=lambda: False):
        self.model, self.on_submit, self.wake = model, on_submit, wake
        self.on_action = on_action  # called with "mute" or "listen"
        self.hotkeys, self.mouse = hotkeys, mouse  # () -> Hotkeys, () -> bool: read at each key, so settings apply at once
        self._old = None
        self._fd = -1
        self._decoder = None
        self._mouse_on = False

    def start(self) -> None:
        import codecs
        import sys
        import termios
        import tty

        self._fd = sys.stdin.fileno()
        self._old = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        self._decoder = codecs.getincrementaldecoder("utf-8")("ignore")
        if self.mouse():
            sys.stdout.write(self.MOUSE_ON)
            sys.stdout.flush()
            self._mouse_on = True
        asyncio.get_running_loop().add_reader(self._fd, self._read)

    def _read(self) -> None:
        import os

        try:
            data = os.read(self._fd, 4096)
        except OSError:
            return
        self.feed(self._decoder.decode(data))

    def feed(self, text: str) -> None:
        """Handle what the terminal sent: keys, clicks, typing."""
        if self.hotkeys is not None:
            self.model.hotkeys = self.hotkeys()
        self.model.mouse = bool(self.mouse())
        for key in parse_keys(text):
            if isinstance(key, Mouse):
                action = click_action(self.model, key)
                if action and self.on_action is not None:
                    self.on_action(action)
                continue
            result = dispatch_key(self.model, key, self.model.hotkeys)
            if result is None:
                continue
            kind, line = result
            if kind == "send":
                asyncio.ensure_future(self.on_submit(line))
            elif self.on_action is not None:
                self.on_action(kind)
        if self.wake is not None:
            self.wake.set()  # redraw at once: the typed letter, or the new colour, shows immediately

    def stop(self) -> None:
        import sys
        import termios

        if self._mouse_on:
            sys.stdout.write(self.MOUSE_OFF)
            sys.stdout.flush()
        if self._fd >= 0:
            try:
                asyncio.get_running_loop().remove_reader(self._fd)
            except (RuntimeError, ValueError):
                pass
            if self._old is not None:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)


class TerminalUI:
    def __init__(self, bus, flags=None, status=None, reduced_motion=lambda: False, images=lambda: "auto", model=None,
                 cover_size=lambda: G_COVER_COLS, on_submit=None, on_action=None, hotkeys=lambda: Hotkeys(),
                 mouse=lambda: False):
        self.model = model or TuiModel(flags, status)
        self._on_action, self._hotkeys, self._mouse = on_action, hotkeys, mouse  # Mute / Listen: keys and clicks
        self._reduced, self._images, self._cover_size = reduced_motion, images, cover_size
        self._on_submit = on_submit  # called with each line typed in the chat bar (None: the bar only shows words)
        self._queue = bus.subscribe({"state", "level", "partial", "transcript", "display", "error", "notice", "notes"})
        self._bus = bus

    async def run(self) -> None:
        import sys

        from rich.console import Console
        from rich.live import Live

        console = Console()
        fd = sys.stdout.fileno()
        graphics = choose_graphics(self._images(), fd)  # asked before the full-screen display starts
        write = lambda text: (console.file.write(text), console.file.flush())  # noqa: E731
        overlay = ImageOverlay(write, graphics.protocol if graphics else None)
        wake = asyncio.Event()
        render_s = 0.0
        keys = (_KeyReader(self.model, self._on_submit, wake, self._on_action, self._hotkeys, self._mouse)
                if self._on_submit is not None and sys.stdin.isatty() else None)
        if keys is not None:
            keys.start()
        with Live(render(self.model, console.width), console=console, screen=True, auto_refresh=False) as live:
            async def pump():
                while True:
                    event = await self._queue.get()
                    self.model.apply(event)
                    if event.get("type") != "level":  # the level meter alone must not keep an idle screen busy
                        wake.set()

            task = asyncio.create_task(pump())
            try:
                while True:
                    self.model.reduced_motion = bool(self._reduced())
                    self.model.cover_cols = int(self._cover_size())
                    if graphics is not None:
                        self.model.gfx = Graphics(graphics.protocol, *cell_pixels(fd))  # the window may have been resized
                        write("\x1b[?2026h")  # one atomic update: the text and the picture arrive together
                    started = time.perf_counter()
                    live.update(render(self.model, console.width, console.height), refresh=True)
                    if graphics is not None:
                        painted = safe_paint_images(self.model, overlay)
                        write("\x1b[?2026l")  # always closed, even after a failure, or the terminal would stay frozen
                        if not painted:
                            graphics = None  # failed once: blocks from here on
                    cost = time.perf_counter() - started
                    render_s = cost if render_s == 0.0 else 0.8 * render_s + 0.2 * cost  # smoothed: one slow frame is not a trend
                    try:
                        await asyncio.wait_for(wake.wait(), frame_delay(self.model, self.model.reduced_motion, render_s))
                    except asyncio.TimeoutError:
                        pass
                    wake.clear()
            finally:
                task.cancel()
                overlay.clear()
                if keys is not None:
                    keys.stop()
                self._bus.unsubscribe(self._queue)
