"""Captions that only ever grow.

Every partial transcript of the same recording is a new guess, and guesses flicker ("recognise speech" /
"wreck a nice beach"). A word is shown as settled only once two guesses in a row agree on it (the
LocalAgreement rule); settled words never change or disappear. What the newest guess says beyond that is shown
as tentative, in a dimmer style.
"""
from __future__ import annotations

import re

_STRIP = re.compile(r"[^\w']", re.UNICODE)


def _norm(word: str) -> str:
    return _STRIP.sub("", word.lower())


class StablePrefix:
    def __init__(self):
        self.committed: list[str] = []
        self._prev: list[str] = []

    def feed(self, text: str) -> tuple[str, str]:
        """(settled words, tentative words) after another guess for the same recording."""
        words = (text or "").split()
        agree = 0
        for a, b in zip(self._prev, words):
            if not _norm(a) or _norm(a) != _norm(b):
                break
            agree += 1
        self._prev = words
        if agree > len(self.committed):
            self.committed = words[:agree]
        n = len(self.committed)
        same = [_norm(w) for w in words[:n]] == [_norm(w) for w in self.committed]
        tail = words[n:] if same else []
        return " ".join(self.committed), " ".join(tail)

    def final(self, text: str) -> str:
        """The recogniser's last word: everything is settled."""
        self.committed = (text or "").split()
        self._prev = list(self.committed)
        return " ".join(self.committed)

    def reset(self) -> None:
        self.committed, self._prev = [], []


class Typewriter:
    """Shows words one by one: about one every 70 ms, catching up faster when it falls behind."""

    def __init__(self, per_second: float = 14.0):
        self._rate = per_second
        self.words: list[str] = []
        self.tentative: list[str] = []
        self._shown = 0.0
        self._at: float | None = None

    def set(self, committed: str, tentative: str = "") -> None:
        new = committed.split()
        if self.words[: len(new)] != new[: len(self.words)] and len(new) < self._shown:
            self._shown = 0.0  # a different sentence: start again
        if not new:
            self._shown = 0.0
        self.words, self.tentative = new, tentative.split()

    def step(self, now: float) -> tuple[str, str]:
        """(settled text shown so far, tentative text) at time `now`."""
        if self._at is None:
            self._at = now
        backlog = len(self.words) - self._shown
        rate = self._rate * (1.0 + max(0.0, backlog - 6) / 6.0)
        self._shown = min(float(len(self.words)), self._shown + max(0.0, now - self._at) * rate)
        self._at = now
        shown = int(self._shown)
        done = shown >= len(self.words)
        return " ".join(self.words[:shown]), " ".join(self.tentative) if done else ""
