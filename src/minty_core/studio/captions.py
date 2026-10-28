"""Burned-in captions: short chunks of the script, timed across the voiceover, written as an ASS subtitle file."""
from __future__ import annotations

import re
from dataclasses import dataclass

LEAD_S, TAIL_S = 0.15, 0.25
_BREAK = re.compile(r"[.!?;:,]$")


@dataclass(frozen=True)
class Caption:
    start: float
    end: float
    text: str


def chunk_text(text: str, seconds: float, max_words: int = 3) -> list[Caption]:
    """Two or three words at a time, each shown for as long as it takes to say, across the length of the voiceover. The timing
    is by length of text, not by listening: good enough for short scripts, not frame-exact."""
    words = str(text or "").split()
    window = seconds - LEAD_S - TAIL_S
    if not words or window < 0.3:
        return []
    groups, current = [], []
    for w in words:
        current.append(w)
        if len(current) >= max_words or _BREAK.search(w):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    weights = [sum(len(w) + 1 for w in g) for g in groups]
    total, t, out = float(sum(weights)), LEAD_S, []
    for g, weight in zip(groups, weights):
        span = window * weight / total
        out.append(Caption(round(t, 3), round(t + span, 3), " ".join(g)))
        t += span
    return out


def _stamp(seconds: float) -> str:
    cs = int(round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _clean(text: str) -> str:
    return " ".join(str(text).replace("\\", "/").replace("{", "(").replace("}", ")").split())


def to_ass(captions: list[Caption], width: int, height: int, font: str = "Sans", size: int | None = None) -> str:
    size = size or height // 18
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,2,2,60,60,{height // 5},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for c in captions:
        lines.append(f"Dialogue: 0,{_stamp(c.start)},{_stamp(c.end)},Default,,0,0,0,,{_clean(c.text)}")
    return "\n".join(lines) + "\n"
