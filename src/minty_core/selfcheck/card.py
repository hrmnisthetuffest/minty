"""The daily summary card (679): counts of what happened today and one tip, drawn as an SVG by my own code (Pillow is not installed)
and turned into a PNG by `rsvg-convert` or headless Chrome, whichever is there; with neither you keep the SVG. Counts only: no
message text, names, health or money details ever go on it."""
from __future__ import annotations

import html
import os
import re
import shutil
import tempfile
import textwrap
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from minty_core.system.runner import Runner

SIZE = 1080
_PATH = re.compile(r"/[A-Za-z0-9_./ +-]{1,300}")
CHROME_NAMES = ("google-chrome-stable", "google-chrome", "chromium")


@dataclass
class CardData:
    day: date
    done: int = 0  # things I did for you (action receipts that ended ok)
    refused: int = 0  # things you declined or I was stopped from doing
    jobs: int = 0  # background jobs that finished today
    habits_done: int = 0
    habits_total: int = 0
    tasks_done: int = 0
    events: int = 0  # calendar events today (a number, never their names)
    tip: str = ""


def card_svg(d: CardData) -> str:
    esc = lambda t: html.escape(str(t), quote=True)  # noqa: E731
    habits = f"{d.habits_done}/{d.habits_total}" if d.habits_total else "–"
    tiles = [("Done for you", str(d.done)), ("Habits", habits), ("Tasks done", str(d.tasks_done)), ("Events today", str(d.events))]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}" role="img" '
             'aria-label="Minty daily card">',
             '<style>text{font-family:system-ui,Helvetica,Arial,sans-serif;fill:#d8dee6}.n{font-size:120px;font-weight:700}'
             '.l{font-size:36px;fill:#8a94a3}.h{font-size:54px;font-weight:700;letter-spacing:6px;fill:#e5484d}'
             '.d{font-size:42px}.t{font-size:38px}.f{font-size:30px;fill:#8a94a3}</style>',
             f'<rect width="{SIZE}" height="{SIZE}" fill="#0b0d10"/>',
             '<text class="h" x="64" y="110">MINTY</text>',
             f'<text class="d" x="64" y="180">{esc(d.day.strftime("%A"))} {d.day.day} {esc(d.day.strftime("%b"))}</text>']
    for i, (label, value) in enumerate(tiles):
        x, y = 64 + (i % 2) * 484, 230 + (i // 2) * 270
        parts.append(f'<rect x="{x}" y="{y}" width="448" height="246" rx="28" fill="#14181d" stroke="#252b33" stroke-width="3"/>')
        parts.append(f'<text class="n" x="{x + 36}" y="{y + 140}">{esc(value)}</text>')
        parts.append(f'<text class="l" x="{x + 36}" y="{y + 200}">{esc(label)}</text>')
    lines = textwrap.wrap(" ".join(str(d.tip).split()), 40)[:4]
    for i, line in enumerate(lines):
        parts.append(f'<text class="t" x="64" y="{820 + i * 54}">{esc(line)}</text>')
    parts.append(f'<text class="f" x="64" y="1030">{d.jobs} job{"s" * (d.jobs != 1)} finished · {d.refused} declined or stopped</text>')
    parts.append("</svg>")
    return "".join(parts)


def gather(services: dict, cfg, day: date, tip: str, now: datetime | None = None) -> CardData:
    now = now or datetime.now()
    midnight = datetime.combine(day, datetime.min.time()).timestamp()
    d = CardData(day=day, tip=tip)
    receipts = services.get("receipts")
    if receipts is not None:
        try:
            rows = receipts.since(midnight)
            d.done = sum(1 for r in rows if r.outcome == "ok")
            d.refused = sum(1 for r in rows if r.outcome in ("declined", "blocked"))
        except Exception:  # noqa: BLE001 — a missing number is a zero, not a failed card
            pass
    jobs = services.get("jobs")
    if jobs is not None:
        try:
            d.jobs = sum(1 for j in jobs.select() if (j.finished or 0) >= midnight and j.status == "done")
        except Exception:  # noqa: BLE001
            pass
    prod = services.get("productivity")
    if prod is not None and prod.active():
        try:
            data = prod.day_data(day)
            d.habits_done, d.habits_total = len(data.habits_done), len(data.habits_done) + len(data.habits_open)
            d.tasks_done = data.tasks_done
        except Exception:  # noqa: BLE001
            pass
    integ = services.get("integrations")
    if integ is not None and cfg.integrations.enabled and cfg.integrations.calendar:
        try:
            lo = datetime.combine(day, datetime.min.time(), tzinfo=integ.tz())
            d.events = len([o for o in integ.calendar().occurrences(lo, lo + timedelta(days=1)) if not o.all_day])
        except Exception:  # noqa: BLE001
            pass
    return d


def _ok_path(p) -> bool:
    return _PATH.fullmatch(str(p)) is not None


def make_runner(timeout: float = 60.0) -> Runner:
    def rsvg(a):
        return len(a) == 6 and a[1:3] == ["-w", str(SIZE)] and a[3] == "-o" and _ok_path(a[4]) and _ok_path(a[5])

    def chrome(a):
        fixed = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                 f"--window-size={SIZE},{SIZE}"]
        return (len(a) == 9 and a[1:6] == fixed and a[6].startswith("--user-data-dir=") and _ok_path(a[6][16:])
                and a[7].startswith("--screenshot=") and _ok_path(a[7][13:]) and a[8].startswith("file://") and _ok_path(a[8][7:]))

    return Runner(allowed={"rsvg-convert", *CHROME_NAMES}, rules={"rsvg-convert": rsvg, **{n: chrome for n in CHROME_NAMES}},
                  timeout=timeout)


async def render_png(svg: str, folder: Path, stem: str, runner=None, prefer: str | None = None) -> Path | None:
    """Write the SVG, then try to make a PNG next to it. Returns the PNG path, or None if no renderer worked (the SVG stays)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", stem)[:60] or "card"
    svg_path, png_path = (folder / f"{slug}.svg").resolve(), (folder / f"{slug}.png").resolve()
    svg_path.write_text(svg, encoding="utf-8")
    os.chmod(svg_path, 0o600)
    runner = runner or make_runner()
    order = ["rsvg", "chrome"] if prefer != "chrome" else ["chrome"]
    if prefer == "rsvg":
        order = ["rsvg"]
    for method in order:
        if method == "rsvg" and runner.have("rsvg-convert"):
            res = await runner.run(["rsvg-convert", "-w", str(SIZE), "-o", str(png_path), str(svg_path)])
        elif method == "chrome":
            name = next((n for n in CHROME_NAMES if runner.have(n)), None)
            if name is None:
                continue
            page = svg_path.with_suffix(".html")
            page.write_text('<!doctype html><meta charset="utf-8"><body style="margin:0;background:#0b0d10">' + svg + "</body>",
                            encoding="utf-8")
            with tempfile.TemporaryDirectory(prefix="minty-card-") as profile:
                res = await runner.run([name, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                                        "--force-device-scale-factor=1", f"--window-size={SIZE},{SIZE}",
                                        f"--user-data-dir={profile}", f"--screenshot={png_path}", f"file://{page}"])
            page.unlink(missing_ok=True)
        else:
            continue
        if res.ok and png_path.is_file() and png_path.stat().st_size > 100:
            os.chmod(png_path, 0o600)
            return png_path
        png_path.unlink(missing_ok=True)
    return None
