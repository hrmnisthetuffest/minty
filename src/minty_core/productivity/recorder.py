"""The lesson / meeting recorder (602), with marks (744) and a consent reminder (755).

Audio exists only inside a session you start by name: the pipeline passes frames here only while `recording` is
true, `feed` outside a session does nothing, and a muted microphone is never recorded. Chunks are written under an
owner-only folder (small enough for the speech-to-text upload limit), turned into text, and then deleted unless
`keep_audio` is on. The note that results carries the consent reminder that was shown. A reminder, not legal advice.
"""
from __future__ import annotations

import os
import shutil
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from minty_core.audio.types import SAMPLE_RATE
from minty_core.study.text import structure_note, summarise

REMINDER = ("Recording starts now. Make sure everyone who can be heard agrees to it. A red REC marker stays on "
            "until you stop.")
CHUNK_BYTES = 20 * 1024 * 1024  # under the 25 MB upload limit of the hosted recogniser
MAX_TRANSCRIPT = 20_000


class RecorderError(RuntimeError):
    pass


@dataclass
class Session:
    id: str
    dir: Path
    label: str
    started: float
    ended: float | None = None
    chunks: list[Path] = field(default_factory=list)
    marks: list[tuple[float, str]] = field(default_factory=list)
    rate: int = SAMPLE_RATE

    @property
    def duration_s(self) -> float:
        return (self.ended or self.started) - self.started


@dataclass
class Result:
    note_id: str
    title: str
    duration_s: float
    words: int
    todos: list[str]
    marks: int


def _clock_text(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class Recorder:
    def __init__(self, folder: Path, clock: Callable[[], float] = time.time, chunk_bytes: int = CHUNK_BYTES,
                 max_minutes: int = 180, keep_audio: bool = False, rate: int = SAMPLE_RATE):
        self.folder, self._clock, self.chunk_bytes = Path(folder), clock, chunk_bytes
        self.max_minutes, self.keep_audio, self.rate = max_minutes, keep_audio, rate
        self.session: Session | None = None
        self.auto_stopped = False
        self._wav: wave.Wave_write | None = None
        self._written = 0

    # ---- state ---------------------------------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self.session is not None and self.session.ended is None

    def elapsed(self) -> float:
        return self._clock() - self.session.started if self.session is not None else 0.0

    def active(self) -> bool:  # the hold-back protocol shared with focus mode
        return self.recording

    def holds(self, event: dict) -> bool:
        return self.recording and not event.get("urgent")

    # ---- a session -----------------------------------------------------------------------------------
    def start(self, label: str = "") -> str:
        if self.recording:
            raise RecorderError("A recording is already running.")
        now = self._clock()
        ident = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"
        folder = self.folder / ident
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        os.chmod(folder, 0o700)
        self.session = Session(ident, folder, " ".join(str(label).split())[:60] or "Recording", now)
        self.auto_stopped = False
        self._open_chunk()
        return REMINDER

    def _open_chunk(self) -> None:
        s = self.session
        path = s.dir / f"part{len(s.chunks) + 1:03d}.wav"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self.rate)
        self._written = 0
        s.chunks.append(path)

    def feed(self, pcm: bytes) -> None:
        if not self.recording or not pcm or self._wav is None:
            return
        if self._clock() - self.session.started > self.max_minutes * 60:
            self.auto_stopped = True
            self.stop()
            return
        if self._written + len(pcm) > self.chunk_bytes:
            self._wav.close()
            self._open_chunk()
        self._wav.writeframes(pcm)
        self._written += len(pcm)

    def mark(self, label: str = "") -> float:
        if not self.recording:
            raise RecorderError("There is no recording to mark.")
        at = round(self.elapsed(), 1)
        self.session.marks.append((at, " ".join(str(label).split())[:80] or "mark"))
        return at

    def stop(self) -> Session:
        if not self.recording:
            raise RecorderError("There is no recording running.")
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        self.session.ended = self._clock()
        return self.session

    # ---- afterwards ----------------------------------------------------------------------------------
    async def finish(self, session: Session, transcribe: Callable[[bytes, int], Awaitable[str]],
                     save: Callable[[str, str, list[str]], str]) -> Result:
        texts = []
        try:
            for chunk in session.chunks:
                with wave.open(str(chunk), "rb") as w:
                    pcm = w.readframes(w.getnframes())
                if not pcm:
                    continue
                text = (await transcribe(pcm, session.rate) or "").strip()
                if text:
                    texts.append(text)
            transcript = " ".join(texts)
            when = time.localtime(session.started)
            title = f"{session.label} {time.strftime('%d %b %Y', when)}"
            lines = [f"# {title}", f"*{_clock_text(session.duration_s)} recorded on this computer*", ""]
            todos: list[str] = []
            words = len(transcript.split())
            if transcript:
                structure = structure_note(transcript, session.label)
                todos = structure.todos
                summary = summarise(transcript, 6)
                lines += ["## Summary", *[f"- {p}" for p in (summary.points or structure.key_points)[:6]], ""]
                lines += ["## Action items", *([f"- [ ] {t}" for t in todos] or ["- none heard"]), ""]
                if structure.questions:
                    lines += ["## Questions", *[f"- {q}" for q in structure.questions], ""]
            else:
                lines += ["No speech was found in this recording.", ""]
            if session.marks:
                lines += ["## Marked moments", *[f"- {_clock_text(at)} {label}" for at, label in session.marks], ""]
            lines += ["## Consent",
                      f"Recorded {time.strftime('%d %b %Y %H:%M', when)}-{time.strftime('%H:%M', time.localtime(session.ended or session.started))} "
                      f"on this computer. Reminder shown at the start: {REMINDER}", ""]
            if transcript:
                lines += ["## Transcript", transcript[:MAX_TRANSCRIPT]]
            note_id = save(title, "\n".join(lines) + "\n", ["recording", "untrusted-speech"])
        finally:
            if not self.keep_audio:
                shutil.rmtree(session.dir, ignore_errors=True)
        return Result(note_id, title, session.duration_s, words, todos, len(session.marks))


class Holds:
    """Several things that can make non-urgent notices wait (focus mode, a recording), seen as one."""

    def __init__(self, *sources):
        self.sources = [s for s in sources if s is not None]

    def holds(self, event: dict) -> bool:
        return any(s.holds(event) for s in self.sources)

    def active(self) -> bool:
        return any(s.active() for s in self.sources)
