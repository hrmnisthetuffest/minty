"""The evaluation gate for meaning-based search (604).

The candidate is plain text search fused with a character-n-gram similarity (no model, no download). It is adopted
only if, on questions *you* approved, it finds the right note in the top five at least ten points more often than
plain search, and answers in 50 ms or less. A heavier embedding model plugs into the same harness (any object with
`vector(text)` and `cosine(a, b)`).
"""
from __future__ import annotations

import json
import math
import platform
import re
import time
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text

MIN_QUESTIONS = 100
MIN_GAIN_POINTS = 10.0
MAX_MS = 50.0
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class GateResult:
    baseline_recall: float
    candidate_recall: float
    gain_points: float
    candidate_ms: float
    questions: int
    passed: bool
    reason: str
    at: str = ""
    machine: str = ""


class HashEmbedder:
    """Feature-hashed character 3- to 5-grams of each word, L2-normalised. Sparse vectors as dicts."""

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def vector(self, text: str) -> dict[int, float]:
        counts: dict[int, float] = {}
        for word in _WORD.findall(text.lower()):
            padded = f"^{word}$"
            for n in (3, 4, 5):
                for i in range(len(padded) - n + 1):
                    h = zlib.crc32(padded[i:i + n].encode("utf-8"))
                    slot = h % self.dim
                    counts[slot] = counts.get(slot, 0.0) + (1.0 if (h >> 16) & 1 else -1.0)
        norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
        return {k: v / norm for k, v in counts.items() if v}

    @staticmethod
    def cosine(a: dict[int, float], b: dict[int, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(k, 0.0) for k, v in a.items())


def rrf(*rankings: list[str], k: int = 60) -> list[str]:
    """Reciprocal rank fusion: ids that several rankings like float to the top."""
    score: dict[str, float] = {}
    for ranking in rankings:
        for rank, ident in enumerate(ranking):
            score[ident] = score.get(ident, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score, key=lambda i: -score[i])


def recall_at_k(ranker: Callable[[str], list[str]], questions: list[tuple[str, str]], k: int = 5) -> float:
    if not questions:
        return 0.0
    return sum(1 for q, want in questions if want in ranker(q)[:k]) / len(questions)


def hybrid_ranker(vault, embedder=None) -> Callable[[str, int], list[str]]:
    embedder = embedder or HashEmbedder()
    cache: dict[str, tuple[str, dict]] = {}

    def vectors() -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row in vault.index.rows(None, limit=100_000):
            hit = cache.get(row["id"])
            if hit is None or hit[0] != row["updated"]:
                note = vault.get(row["id"])
                if note is None:
                    continue
                hit = (row["updated"], embedder.vector(f"{note.title} {note.title} {note.body} {' '.join(note.tags)}"))
                cache[row["id"]] = hit
            out[row["id"]] = hit[1]
        return out

    def rank(query: str, limit: int = 5) -> list[str]:
        vault.refresh()
        text = vault.search_ids(query, limit=20)
        q = embedder.vector(query)
        sims = sorted(((embedder.cosine(q, v), i) for i, v in vectors().items()), reverse=True)
        semantic = [i for s, i in sims[:20] if s > 0.05]
        return rrf(text, semantic)[:limit]

    return rank


def evaluate(vault, questions: list[tuple[str, str]], embedder=None, *, k: int = 5,
             min_gain: float = MIN_GAIN_POINTS, max_ms: float = MAX_MS, min_questions: int = MIN_QUESTIONS,
             clock: Callable[[], float] = time.time) -> GateResult:
    base = recall_at_k(lambda q: vault.search_ids(q, limit=k), questions, k)
    rank = hybrid_ranker(vault, embedder)
    if questions:
        rank(questions[0][0], k)  # warm the vector cache; a query pays only for itself afterwards
    times: list[float] = []
    cand_hits = 0
    for q, want in questions:
        t = time.perf_counter()
        got = rank(q, k)
        times.append((time.perf_counter() - t) * 1000)
        cand_hits += want in got[:k]
    cand = cand_hits / len(questions) if questions else 0.0
    times.sort()
    p95 = times[int(0.95 * (len(times) - 1))] if times else 0.0
    gain = (cand - base) * 100
    if len(questions) < min_questions:
        passed, reason = False, (f"only {len(questions)} question{'s' * (len(questions) != 1)}; "
                                 f"the gate needs {min_questions} questions")
    elif gain < min_gain:
        passed, reason = False, f"the recall gain is {gain:.1f} points; it needs {min_gain:.0f}"
    elif p95 > max_ms:
        passed, reason = False, f"a query took {p95:.1f} ms; the limit is {max_ms:.0f} ms"
    else:
        passed, reason = True, f"recall at {k} improved by {gain:.1f} points at {p95:.1f} ms a query"
    return GateResult(round(base, 4), round(cand, 4), round(gain, 1), round(p95, 2), len(questions), passed, reason,
                      datetime.fromtimestamp(clock()).isoformat(timespec="seconds"), platform.node())


def save_result(path: Path, result: GateResult) -> None:
    atomic_write_text(Path(path), json.dumps(asdict(result), indent=2))


def load_result(path: Path) -> GateResult | None:
    try:
        return GateResult(**json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def load_questions(path: Path, vault) -> list[tuple[str, str]]:
    """[{"q": "...", "note": "<note id or exact title>"}, ...] -> [(question, note id)]."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    by_title = {n.title.lower(): n.id for n in vault.list(None, limit=100_000)}
    out = []
    for item in raw:
        q, want = str(item["q"]), str(item["note"])
        ident = want if vault.get(want) is not None else by_title.get(want.lower())
        if ident is None:
            raise ValueError(f"No note matches {want!r} for the question {q!r}.")
        out.append((q, ident))
    return out
