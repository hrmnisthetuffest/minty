"""What a provider's response headers say about its rate limits (Groq's x-ratelimit-*, OpenRouter's X-RateLimit-*,
retry-after). Providers that send none simply give no information; the configured limits apply then."""
from __future__ import annotations

import re
from dataclasses import dataclass

_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


@dataclass
class RateInfo:
    limit_requests: int | None = None
    remaining_requests: int | None = None
    reset_requests_s: float | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    reset_tokens_s: float | None = None


def parse_duration(text: str) -> float | None:
    """'7.66s', '2m59.56s', '1h2m3s', '120ms' or plain seconds -> seconds; None when unreadable."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    parts = _PART.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    return sum(float(n) * scale[u] for n, u in parts)


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_rate_headers(headers, now: float) -> RateInfo | None:
    h = {str(k).lower(): str(v) for k, v in dict(headers).items()}
    info = RateInfo(
        limit_requests=_int(h.get("x-ratelimit-limit-requests", h.get("x-ratelimit-limit"))),
        remaining_requests=_int(h.get("x-ratelimit-remaining-requests", h.get("x-ratelimit-remaining"))),
        limit_tokens=_int(h.get("x-ratelimit-limit-tokens")),
        remaining_tokens=_int(h.get("x-ratelimit-remaining-tokens")),
        reset_tokens_s=parse_duration(h.get("x-ratelimit-reset-tokens", "")),
    )
    if "x-ratelimit-reset-requests" in h:
        info.reset_requests_s = parse_duration(h["x-ratelimit-reset-requests"])
    elif "x-ratelimit-reset" in h:  # OpenRouter: when the window resets, in epoch milliseconds
        at = _int(h["x-ratelimit-reset"])
        info.reset_requests_s = max(0.0, at / 1000.0 - now) if at is not None else None
    if all(v is None for v in vars(info).values()):
        return None
    return info
