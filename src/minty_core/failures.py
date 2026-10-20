"""Failure injection: make provider calls fail on purpose so the reliability work stays honest.

Settings `debug.inject` map a failure kind to the share of provider calls it hits, `debug.only` limits it to named
providers, `debug.seed` makes a run repeatable. Everything is off by default. The faults are raised exactly where a
real provider's error would surface, so the router's classification, cooling and fail-over run for real.
"""
from __future__ import annotations

import asyncio
import random

from minty_core.llm.types import LLMError, llm_error

# kind -> (HTTP status that real providers use, message)
KINDS: dict[str, tuple[int | None, str]] = {
    "rate_limit": (429, "injected: rate limit exceeded"),
    "too_large": (413, "injected: request too large for this model"),
    "server": (503, "injected: service unavailable"),
    "auth": (401, "injected: invalid api key"),
    "timeout": (None, "injected: timed out waiting for the model"),
}


class FaultInjector:
    def __init__(self):
        self._rng = random.Random()
        self._seeded: int | None = object()  # force the first configure to seed

    def configure(self, seed: int | None) -> None:
        if seed != self._seeded:
            self._rng = random.Random(seed)
            self._seeded = seed

    def pick(self, rates: dict[str, float], provider: str, only: list[str]) -> str | None:
        """A failure kind for this call, or None. One draw per call, so rates behave as shares of calls."""
        if not rates or (only and provider not in only):
            return None
        draw, total = self._rng.random(), 0.0
        for kind, rate in rates.items():
            if kind not in KINDS or rate <= 0:
                continue
            total += min(1.0, float(rate))
            if draw < total:
                return kind
        return None

    @staticmethod
    def error(kind: str, provider: str) -> LLMError:
        status, message = KINDS[kind]
        err = llm_error(f"{provider}: {message}", status=status)
        if kind == "timeout":
            err.kind = "timeout"
        err.provider = provider
        return err


async def failing_stream(error: LLMError, delay_s: float = 0.0):
    """An event stream that fails the way a real one does, after an optional wait (a slow provider)."""
    if delay_s > 0:
        await asyncio.sleep(delay_s)
    raise error
    yield  # noqa: unreachable — makes this an async generator
