"""A small FSRS-4.5 scheduler with the published default parameters (an own implementation of the public formulas,
not the py-fsrs package). Deterministic: no fuzz, no randomness. Swap it for py-fsrs behind `Scheduler.review`."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
DAY = 86400.0
W = (0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031, 1.6474, 0.1367, 1.0461, 2.1072, 0.0793, 0.3246,
     1.587, 0.2272, 2.8755)
DECAY = -0.5
FACTOR = 0.9 ** (1 / DECAY) - 1  # 19/81
RELEARN_S = 10 * 60.0


@dataclass(frozen=True)
class CardState:
    stability: float
    difficulty: float
    due: float
    last_review: float | None
    reps: int = 0
    lapses: int = 0
    state: str = "review"  # learning | review | relearning


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class Scheduler:
    def __init__(self, w=W, retention: float = 0.9, max_interval_days: int = 365):
        self.w, self.retention, self.max_interval = w, retention, max_interval_days

    def retrievability(self, elapsed_days: float, stability: float) -> float:
        return (1 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY

    def interval_days(self, stability: float) -> int:
        days = stability / FACTOR * (self.retention ** (1 / DECAY) - 1)
        return int(_clamp(round(days), 1, self.max_interval))

    def _d0(self, grade: int) -> float:
        return _clamp(self.w[4] - (grade - 3) * self.w[5], 1.0, 10.0)

    def review(self, card: CardState | None, grade: int, now: float) -> CardState:
        if grade not in (AGAIN, HARD, GOOD, EASY):
            raise ValueError("grade is 1 (again) to 4 (easy)")
        w = self.w
        if card is None or card.reps == 0:
            s, d = w[grade - 1], self._d0(grade)
            reps, lapses = 1, 1 if grade == AGAIN else 0
        else:
            elapsed = max(0.0, (now - (card.last_review or now)) / DAY)
            r = self.retrievability(elapsed, card.stability)
            d = card.difficulty - w[6] * (grade - 3)
            d = _clamp(w[7] * self._d0(3) + (1 - w[7]) * d, 1.0, 10.0)
            if grade == AGAIN:
                s = w[11] * card.difficulty ** -w[12] * ((card.stability + 1) ** w[13] - 1) * math.exp(w[14] * (1 - r))
                s = min(s, card.stability)
            else:
                bonus = (w[15] if grade == HARD else 1.0) * (w[16] if grade == EASY else 1.0)
                s = card.stability * (1 + math.exp(w[8]) * (11 - card.difficulty) * card.stability ** -w[9]
                                      * (math.exp(w[10] * (1 - r)) - 1) * bonus)
            reps, lapses = card.reps + 1, card.lapses + (1 if grade == AGAIN else 0)
        s = max(0.01, s)
        if grade == AGAIN:
            return CardState(s, d, now + RELEARN_S, now, reps, lapses, "relearning")
        return CardState(s, d, now + self.interval_days(s) * DAY, now, reps, lapses, "review")
