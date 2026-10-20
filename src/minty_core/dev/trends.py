"""Trace trends (656): per-day numbers from the trace files as sparklines for the ear and eye, and a script-free SVG page."""
from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from minty_core.trace import read_events
from minty_core.tracereport import summarise

BLOCKS = "▁▂▃▄▅▆▇█"


@dataclass
class Day:
    date: date
    turns: int = 0
    first_word_p50_ms: float | None = None
    first_word_p95_ms: float | None = None
    failovers: int = 0
    tool_error_rate: float | None = None
    est_tokens: int = 0


def daily_metrics(directory: Path, days: int = 14, today: date | None = None) -> list[Day]:
    today = today or date.today()
    out = []
    for back in range(days - 1, -1, -1):
        d = today - timedelta(days=back)
        events = read_events(Path(directory), d.isoformat())
        if not events:
            out.append(Day(d))
            continue
        s = summarise(events)
        out.append(Day(d, s["turns"], s["first_word_p50_ms"], s["first_word_p95_ms"],
                       sum(s["failovers_by_provider"].values()), s["tool_error_rate"], s["est_tokens"]))
    return out


def sparkline(values: list) -> str:
    nums = [v for v in values if v is not None]
    if not nums:
        return "·" * len(values)
    lo, hi = min(nums), max(nums)
    out = []
    for v in values:
        if v is None:
            out.append("·")
        elif hi == lo:
            out.append(BLOCKS[3])
        else:
            out.append(BLOCKS[min(7, int((v - lo) / (hi - lo) * 7.999))])
    return "".join(out)


METRICS = [("Turns", lambda d: d.turns if d.turns else None, None, "{:.0f}"),
           ("First word", lambda d: d.first_word_p50_ms / 1000 if d.first_word_p50_ms is not None else None, "lower",
            "{:.1f} s"),
           ("Fallbacks", lambda d: d.failovers if d.turns else None, "lower", "{:.0f}"),
           ("Tool errors", lambda d: d.tool_error_rate * 100 if d.tool_error_rate is not None else None, "lower",
            "{:.0f}%"),
           ("Tokens", lambda d: d.est_tokens if d.turns else None, None, "{:.0f}")]


def _week_change(values: list) -> float | None:
    last = [v for v in values[-7:] if v is not None]
    before = [v for v in values[-14:-7] if v is not None]
    if not last or not before or sum(before) == 0:
        return None
    return (sum(last) / len(last)) / (sum(before) / len(before)) - 1


def trend_text(days: list[Day]) -> str:
    if not any(d.turns for d in days):
        return "No traces yet, so there is nothing to chart."
    lines = [f"Last {len(days)} days, oldest to newest:"]
    for label, pick, good, fmt in METRICS:
        values = [pick(d) for d in days]
        latest = next((v for v in reversed(values) if v is not None), None)
        change = _week_change(values)
        note = ""
        if change is not None and abs(change) >= 0.15:
            if good == "lower":
                note = ", slower" if label == "First word" and change > 0 else \
                    (", worse" if change > 0 else ", better")
            else:
                note = ", busier" if change > 0 else ", quieter"
            note += f" than last week ({change:+.0%})"
        lines.append(f"{label}: {sparkline(values)} (latest {fmt.format(latest) if latest is not None else 'none'}{note})")
    return "\n".join(lines)


def trend_svg(days: list[Day], width: int = 640, row_h: int = 70) -> str:
    esc = html.escape
    height = row_h * len(METRICS) + 20
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Trace trends">', '<style>text{font:12px system-ui,sans-serif;fill:currentColor}'
             'polyline{fill:none;stroke:#0b57d0;stroke-width:2}.g{stroke:#8885;stroke-width:1}</style>']
    n = max(1, len(days) - 1)
    for i, (label, pick, _good, fmt) in enumerate(METRICS):
        top = 10 + i * row_h
        values = [pick(d) for d in days]
        nums = [v for v in values if v is not None]
        parts.append(f'<text x="8" y="{top + 12}">{esc(label)}</text>')
        parts.append(f'<line class="g" x1="8" x2="{width - 8}" y1="{top + row_h - 12}" y2="{top + row_h - 12}"/>')
        if nums:
            lo, hi = min(nums), max(nums)
            span = (hi - lo) or 1
            pts = " ".join(f"{8 + (width - 16) * k / n:.1f},{top + row_h - 16 - (v - lo) / span * (row_h - 36):.1f}"
                           for k, v in enumerate(values) if v is not None)
            parts.append(f'<polyline points="{pts}"/>')
            parts.append(f'<text x="{width - 8}" y="{top + 12}" text-anchor="end">{esc(fmt.format(nums[-1]))} now, '
                         f'{esc(fmt.format(hi))} max</text>')
    parts.append("</svg>")
    return "".join(parts)


def write_chart(folder: Path, days: list[Day], now: date | None = None) -> Path:
    now = now or date.today()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"trends-{now:%Y%m%d}.html"
    path.write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Trace trends</title>'
        '<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}</style></head>'
        f"<body><h1>Trace trends</h1>{trend_svg(days)}</body></html>", encoding="utf-8")
    os.chmod(path, 0o600)
    return path
