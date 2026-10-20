import time

import httpx

from minty_core import destinations

from minty_core.llm.ratelimit import parse_rate_headers
from minty_core.llm.types import LLMError, llm_error

_client: httpx.AsyncClient | None = None
# keep a provider's connection open between turns (httpx closes idle ones after 5 s), so the next request skips the
# TCP and TLS handshake; the start-up key probe opens them in the first place
KEEPALIVE_S = 60.0


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0),
                                    limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S),
                                    event_hooks=destinations.hooks())
    return _client


def error_from_response(provider: str, status: int, headers, body: str) -> LLMError:
    retry_after = None
    raw = headers.get("retry-after")
    if raw:
        try:
            retry_after = float(raw)
        except ValueError:
            pass
    err = llm_error(f"{provider}: HTTP {status} {body[:200]}", status=status, retry_after=retry_after)
    err.rate = parse_rate_headers(headers, now=time.time())
    return err
