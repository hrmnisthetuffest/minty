"""How Minty sounds: your pronunciation dictionary (519), whisper mode (511), matching the room (741), matching your
pace (742), the evening profile (521) and lowering other audio instead of pausing it (740). Each is small arithmetic
or a text substitution; the switches are in `voice.*`."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

WHISPER_GAIN = 0.35
NORMAL_WPS = 2.5  # words a second in ordinary speech


class Pronunciations:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self._entries = {str(k).lower(): str(v) for k, v in json.loads(self.path.read_text("utf-8")).items()}
        except (OSError, ValueError, AttributeError):
            self._entries = {}

    def entries(self) -> dict[str, str]:
        return dict(self._entries)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._entries, f)

    def add(self, word: str, spoken: str) -> None:
        word, spoken = word.strip().lower(), spoken.strip()
        if not word or not spoken or len(self._entries) >= 500:
            raise ValueError("a word and how to say it")
        self._entries[word] = spoken
        self._save()

    def remove(self, word: str) -> bool:
        gone = self._entries.pop(word.strip().lower(), None) is not None
        if gone:
            self._save()
        return gone

    def apply(self, text: str) -> str:
        for word, spoken in self._entries.items():
            text = re.sub(rf"\b{re.escape(word)}\b", lambda m, s=spoken: s, text, flags=re.IGNORECASE)
        return text


_TEACH = re.compile(r"^(?:say|pronounce)\s+(.+?)\s+as\s+(.+)$", re.IGNORECASE)


def parse_pronunciation(text: str) -> tuple[str, str] | None:
    m = _TEACH.match(" ".join(text.strip().rstrip(".!?").split()))
    return (m.group(1), m.group(2)) if m else None


def ambient_gain(noise_dbfs: float | None) -> float:
    """Louder in a loud room, quieter in a quiet one, within 0.7 to 1.5."""
    if noise_dbfs is None:
        return 1.0
    if noise_dbfs <= -60:
        return 0.7
    if noise_dbfs <= -45:
        return 0.7 + 0.3 * (noise_dbfs + 60) / 15
    if noise_dbfs <= -30:
        return 1.0 + 0.5 * (noise_dbfs + 45) / 15
    return 1.5


def pace_factor(words_per_s: float | None) -> float:
    if words_per_s is None:
        return 1.0
    return max(0.85, min(1.15, 1.0 + (words_per_s - NORMAL_WPS) * 0.1))


def time_profile(profiles: list[dict], now: datetime) -> dict:
    hhmm = now.strftime("%H:%M")
    for p in profiles:
        start, end = str(p.get("from", "")), str(p.get("to", ""))
        inside = (start <= hhmm or hhmm < end) if start > end else start <= hhmm < end
        if start and end and inside:
            return {"gain": float(p.get("gain", 1.0)), "speed": float(p.get("speed", 1.0))}
    return {"gain": 1.0, "speed": 1.0}


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return done.returncode, done.stdout


class Ducker:
    """Lower other applications' playback while Minty speaks (PipeWire/PulseAudio), and put it back. Never touches its
    own stream."""

    def __init__(self, run: Callable[[list[str]], tuple[int, str]] = _run, own_pid: int | None = None,
                 level: float = 0.3):
        self._run, self._pid, self.level = run, own_pid if own_pid is not None else os.getpid(), level
        self._saved: dict[str, int] = {}
        self._lock = threading.Lock()

    def _inputs(self) -> list[tuple[str, int, str]]:
        code, out = self._run(["pactl", "list", "sink-inputs"])
        if code != 0:
            return []
        found = []
        for block in re.split(r"(?m)^Sink Input #", out)[1:]:
            ident = block.split("\n", 1)[0].strip()
            vol = re.search(r"Volume:.*?(\d+)%", block)
            pid = re.search(r'application\.process\.id = "(\d+)"', block)
            found.append((ident, int(vol.group(1)) if vol else 100, pid.group(1) if pid else ""))
        return found

    def begin(self) -> bool:
        with self._lock:
            if self._saved:
                return False
            for ident, pct, pid in self._inputs():
                if pid == str(self._pid):
                    continue
                self._saved[ident] = pct
                self._run(["pactl", "set-sink-input-volume", ident, f"{int(pct * self.level)}%"])
            return bool(self._saved)

    def restore(self) -> None:
        with self._lock:
            saved, self._saved = self._saved, {}
        for ident, pct in saved.items():
            self._run(["pactl", "set-sink-input-volume", ident, f"{pct}%"])
