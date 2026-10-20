"""Speaker verification (508): a convenience check, never the only check for anything that matters.

The model that turns audio into a voice embedding is not bundled and not downloaded; this is the enrol and compare
logic around an `extractor(pcm) -> vector` you provide. With none it says so and blocks nothing. A failed check only
raises scrutiny (an extra question), it never locks you out."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np


@dataclass
class VerifyResult:
    ok: bool | None  # None: no check could be made
    score: float | None = None
    note: str = ""
    raise_scrutiny: bool = False


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n else v


class SpeakerVerifier:
    def __init__(self, extractor: Callable[[bytes], np.ndarray] | None, path: Path | None = None):
        self._extract = extractor
        self.path = Path(path) if path else None
        self._centroid: np.ndarray | None = None
        self.threshold = 0.7
        if self.path and self.path.is_file():
            try:
                data = json.loads(self.path.read_text("utf-8"))
                self._centroid, self.threshold = np.array(data["centroid"]), float(data["threshold"])
            except (OSError, ValueError, KeyError):
                pass

    def available(self) -> bool:
        return self._extract is not None

    def enrolled(self) -> bool:
        return self._centroid is not None

    def enrol(self, samples: list[bytes]) -> None:
        if self._extract is None:
            raise RuntimeError("no speaker model installed")
        if len(samples) < 3:
            raise ValueError("enrolment needs at least three samples of your voice")
        vecs = [_unit(np.asarray(self._extract(s), dtype=float)) for s in samples]
        centroid = _unit(np.mean(vecs, axis=0))
        sims = [float(v @ centroid) for v in vecs]
        self._centroid = centroid
        self.threshold = float(min(0.85, max(0.5, np.mean(sims) - 3 * np.std(sims))))
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"centroid": self._centroid.tolist(), "threshold": self.threshold}, f)

    def verify(self, pcm: bytes) -> VerifyResult:
        if self._extract is None:
            return VerifyResult(None, None, "no speaker model installed, so I can't check whose voice that was")
        if self._centroid is None:
            return VerifyResult(None, None, "your voice isn't enrolled yet")
        score = float(_unit(np.asarray(self._extract(pcm), dtype=float)) @ self._centroid)
        ok = score >= self.threshold
        return VerifyResult(ok, round(score, 3), "" if ok else "that didn't sound like you", raise_scrutiny=not ok)
