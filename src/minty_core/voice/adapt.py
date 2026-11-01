"""How Minty sounds: your pronunciation dictionary (519), whisper mode (511), matching the room (741), matching your
pace (742), the evening profile (521) and lowering other audio instead of pausing it (740). Each is small arithmetic
or a text substitution; the switches are in `voice.*`."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

WHISPER_GAIN = 0.35
NORMAL_WPS = 2.5  # words a second in ordinary speech


class Pronunciations:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self._entries = {str(k).lower(): str(v) for k, v in json.loads(self.path.read_text(encoding="utf-8")).items()}
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
        done = subprocess.run(argv, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return done.returncode, done.stdout


OWED_DAYS = 2  # how long a stream's turned-down volume is still taken for Minty's doing


def _app(name: str, binary: str, pid: str) -> str:
    """Who a stream belongs to, the way the sound server remembers volumes: by application name."""
    return name or binary or (f"pid:{pid}" if pid else "")


class Ducker:
    """Lower other applications' playback (PipeWire/PulseAudio) and put it back. Never touches its own stream.

    The sound server remembers the volume last set on a stream *per application*, so a stream turned down and then closed (a
    browser does that whenever a video stops) leaves the next stream of that app starting quiet, and Minty would take that quiet
    for the normal volume next time. So: streams are matched by app (a stream number is used again by something else), a new
    stream of a turned-down app is turned up too, and when no stream was left to turn up the volume is *owed*: written down,
    paid by the first stream of that app that shows up (`repair`, which a small watcher calls while anything is owed), and paid
    before the next turn-down reads the volume.

    With a `state_path` all of it is written to a file, so if Minty is killed before it could turn the sound back up, the next
    start does it (`recover`) and nothing is left quiet."""

    def __init__(self, run: Callable[[list[str]], tuple[int, str]] = _run, own_pid: int | None = None,
                 level: float = 0.3, state_path: Path | str | None = None, clock: Callable[[], float] = time.time, poll_s: float = 2.0):
        self._run, self._pid, self.level = run, own_pid if own_pid is not None else os.getpid(), level
        self._saved: dict[str, tuple[int, str, int]] = {}  # stream -> (volume it had, app, volume it was turned down to)
        self._owed: dict[str, dict] = {}  # app -> {"was", "to", "ts"}
        self._lock = threading.RLock()
        self._state, self._clock, self._poll_s, self._watching = (Path(state_path) if state_path else None), clock, poll_s, False

    def _inputs(self) -> list[tuple[str, int, str, str]]:
        code, out = self._run(["pactl", "list", "sink-inputs"])
        if code != 0:
            return []
        found = []
        for block in re.split(r"(?m)^Sink Input #", out)[1:]:
            ident = block.split("\n", 1)[0].strip()
            vol = re.search(r"Volume:.*?(\d+)%", block)
            pid = re.search(r'application\.process\.id = "(\d+)"', block)
            name = re.search(r'application\.name = "([^"]*)"', block)
            binary = re.search(r'application\.process\.binary = "([^"]*)"', block)
            pid = pid.group(1) if pid else ""
            found.append((ident, int(vol.group(1)) if vol else 100, pid, _app(name.group(1) if name else "", binary.group(1) if binary else "", pid)))
        return found

    def _write_state(self) -> None:
        if self._state is None:
            return
        if not self._saved and not self._owed:
            self._forget_state()
            return
        saved = {i: {"pct": p, "app": a, "to": t} for i, (p, a, t) in self._saved.items()}
        try:
            self._state.parent.mkdir(parents=True, exist_ok=True)
            self._state.write_text(json.dumps({"saved": saved, "owed": self._owed}), encoding="utf-8")
        except OSError:
            pass

    def _forget_state(self) -> None:
        if self._state is not None:
            try:
                self._state.unlink()
            except OSError:
                pass

    def _set(self, ident: str, pct: int) -> bool:
        return self._run(["pactl", "set-sink-input-volume", ident, f"{pct}%"])[0] == 0

    def _quiet(self, pct: int, was: int, to: int) -> bool:
        """Still at (about) the turned-down volume: not something you set yourself."""
        return pct <= to + 1 and pct < was

    def begin(self) -> bool:
        with self._lock:
            if self._saved:
                return False
            self._repair()  # first: a stream a failed turn-up left quiet must not be read as the normal volume
            for ident, pct, pid, app in self._inputs():
                if pid == str(self._pid):
                    continue
                self._saved[ident] = (pct, app, int(pct * self.level))
            self._write_state()  # before anything is lowered: a kill in the middle still leaves a note
            for ident, (pct, _app_name, _to) in self._saved.items():
                self._set(ident, int(pct * self.level))
            return bool(self._saved)

    def restore(self) -> None:
        with self._lock:
            saved, self._saved = self._saved, {}
            self._settle(saved)

    def recover(self) -> None:
        """After a start: if the last run was killed while the sound was turned down (or left a volume owed), put it right."""
        if self._state is None:
            return
        try:
            data = json.loads(self._state.read_text(encoding="utf-8"))
            saved = {str(i): (int(v["pct"]), str(v["app"]), int(v["to"])) for i, v in data["saved"].items()}
            owed = {str(a): {"was": int(v["was"]), "to": int(v["to"]), "ts": float(v["ts"])} for a, v in data["owed"].items()}
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            self._forget_state()  # damaged, or from before streams were matched by app: a stream number alone proves nothing
            return
        with self._lock:
            self._owed.update(owed)
            self._settle(saved)
            self._repair()  # what was owed before the restart, if a stream of that app is here now

    def repair(self) -> int:
        """Turn up the streams of apps that are owed a volume. Returns how many apps are still owed."""
        with self._lock:
            if self._saved:
                return len(self._owed)  # turned down on purpose right now
            self._repair()
            return len(self._owed)

    # ---- the parts that run under the lock -------------------------------------------------------------------------------
    def _repair(self) -> None:
        now = self._clock()
        self._owed = {a: o for a, o in self._owed.items() if now - o["ts"] < OWED_DAYS * 86400}
        if self._owed:
            seen = set()
            for ident, pct, pid, app in self._inputs():
                debt = self._owed.get(app)
                if debt is None or pid == str(self._pid):
                    continue
                seen.add(app)
                if self._quiet(pct, debt["was"], debt["to"]):
                    self._set(ident, debt["was"])
            for app in seen:
                self._owed.pop(app, None)  # paid, or you have set your own volume since
        self._write_state()

    def _settle(self, saved: dict[str, tuple[int, str, int]]) -> None:
        """Put back what was turned down: the streams that are still there, new streams of the same apps, and a note of what is owed."""
        live = {i: (p, a) for i, p, pid, a in self._inputs() if pid != str(self._pid)}
        for ident, (pct, app, _to) in saved.items():
            if ident in live and live[ident][1] == app:  # the same app still has that stream (numbers are used again)
                if self._set(ident, pct):
                    live[ident] = (pct, app)
                else:
                    del live[ident]
        was: dict[str, tuple[int, int]] = {}
        for pct, app, to in saved.values():
            if app:
                was[app] = (max(pct, was.get(app, (0, 0))[0]), to)
        for ident, (pct, app) in list(live.items()):
            if app in was and ident not in saved and self._quiet(pct, *was[app]) and self._set(ident, was[app][0]):
                live[ident] = (was[app][0], app)
        present = {app for _, app in live.values()}
        now = self._clock()
        for app, (pct, to) in was.items():
            if app in present:
                self._owed.pop(app, None)
            else:  # nothing of that app is left to turn up, and the server still remembers the low volume for its next stream
                self._owed[app] = {"was": pct, "to": to, "ts": now}
        self._write_state()
        if self._owed:
            self._watch()

    def _watch(self) -> None:
        """While a volume is owed, look for the stream that will pay it."""
        if self._poll_s <= 0 or self._watching:
            return
        self._watching = True

        def loop():
            try:
                while True:
                    time.sleep(self._poll_s)
                    with self._lock:
                        if not self._saved:
                            self._repair()
                        if not self._owed:
                            self._watching = False
                            return
            except BaseException:
                self._watching = False
                raise

        threading.Thread(target=loop, daemon=True, name="duck-repair").start()
