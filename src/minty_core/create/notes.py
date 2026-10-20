"""Lecture and podcast notes (666 772): summary, chapters with times, verbatim quotes and links from a transcript, or from an audio
file transcribed in chunks. The extraction is extractive (sentences from the transcript), so nothing is invented, and the notes
always carry a Source line (file, short hash, length) so they can be traced and re-checked."""
from __future__ import annotations

import re
import wave
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from minty_core.study.text import summarise

_URL = re.compile(r"https?://[^\s)\]>\"']+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_STOP = set("the a an and or but of to in on at for with is are was were be been it this that these those as by from not i you "
            "we they he she so if then than there their have has had do does did will would can could should about into".split())


@dataclass
class Chapter:
    start: float | None
    title: str


@dataclass
class Quote:
    start: float | None
    text: str


@dataclass
class Notes:
    markdown: str
    summary: list[str] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


def stamp(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def build_notes(title: str, segments: list[tuple[float | None, str]], source: str, sha: str, duration: float | None,
                when: str) -> Notes:
    segments = [(t, " ".join(x.split())) for t, x in segments if x and x.strip()]
    if not segments:
        raise ValueError("The transcript is empty, so there is nothing to make notes from.")
    title = " ".join(re.sub(r"[<>]", "", title).split())[:120] or "Untitled"
    full = " ".join(x for _, x in segments)
    points = summarise(full, 6).points
    chapters = []
    for i, (start, text) in enumerate(segments, 1):
        lead = (summarise(text, 1).points or [text])[0]
        chapters.append(Chapter(start, " ".join(lead.split())[:70].rstrip(" .,;:")))
    freq = Counter(w.lower() for w in _WORD.findall(full) if w.lower() not in _STOP)
    candidates = []
    for start, text in segments:
        for sent in _sentences(text):
            words = _WORD.findall(sent)
            if 8 <= len(words) <= 30:
                score = sum(freq[w.lower()] for w in set(words) if w.lower() not in _STOP) / (len(words) ** 0.5)
                candidates.append((score, start, sent))
    quotes, used = [], set()
    for score, start, sent in sorted(candidates, key=lambda c: -c[0]):
        if (start, sent) in used or sum(1 for q in quotes if q.start == start) >= 1:
            continue
        quotes.append(Quote(start, sent))
        used.add((start, sent))
        if len(quotes) == 3:
            break
    links = list(dict.fromkeys(m.rstrip(".,;:!?") for m in _URL.findall(full)))[:10]
    length = f", {stamp(duration)} long" if duration else ""
    cite = f"Source: {source}" + (f" (sha256 {sha[:8]}…)" if sha else "") + f"{length}, notes made {when}."
    lines = [f"# Show notes: {title}", "", f"*{cite} Made from a speech-recogniser transcript, so names and numbers may be wrong.*",
             "", "## Summary", *[f"- {p}" for p in points], "", "## Chapters"]
    for i, c in enumerate(chapters, 1):
        label = f"[{stamp(c.start)}]" if c.start is not None else f"[Part {i}]"
        lines.append(f"- {label} {c.title}")
    if quotes:
        lines += ["", "## Quotes"] + [f"> {q.text} {('— [' + stamp(q.start) + ']') if q.start is not None else ''}".rstrip()
                                      for q in quotes]
    if links:
        lines += ["", "## Links"] + [f"- {u}" for u in links]
    return Notes("\n".join(lines) + "\n", points, chapters, quotes, links)


async def transcribe_file(path, media, transcribe: Callable[[bytes, int], Awaitable[str]], chunk_s: float = 300.0
                          ) -> list[tuple[float, str]]:
    duration = (await media.info(path))["duration"]
    media.out_dir.mkdir(parents=True, exist_ok=True)
    out, start, i = [], 0.0, 0
    while start < duration - 0.05:
        piece = media.out_dir / f".chunk-{i}-{abs(hash(str(path))) % 10**6}.wav"
        try:
            await media.chunk_wav(path, start, min(chunk_s, duration - start), piece)
            with wave.open(str(piece), "rb") as w:
                pcm = w.readframes(w.getnframes())
        finally:
            piece.unlink(missing_ok=True)
        text = (await transcribe(pcm, 16000) or "").strip()
        if text:
            out.append((start, text))
        start += chunk_s
        i += 1
    return out
