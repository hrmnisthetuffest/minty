"""Netguard-checked HTTP fetching shared by the apis and files plugins: DNS-pinned, manual redirects, size caps."""
import asyncio
import ipaddress
import logging
from urllib.parse import urljoin, urlsplit

import httpx

from minty_core.httpclient import get_client
from minty_core.netguard import resolve_public

log = logging.getLogger(__name__)

MAX_DOWNLOAD = 1_000_000
MAX_REDIRECTS = 3
TOTAL_TIMEOUT = 60.0
MAX_ADDRESSES = 3  # validated addresses tried per hop when connecting fails
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Minty)", "Accept": "application/json, text/*;q=0.9, */*;q=0.1",
            "Accept-Encoding": "identity"}


class FetchFailed(Exception):
    """A request that could not complete; the message is safe to show (no URL, no secrets)."""




def _origin(url: str) -> tuple:
    parts = urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port


class Fetched:
    def __init__(self, status: int, content_type: str, data: bytes, charset: str, truncated: bool = False,
                 url: str = ""):
        self.status, self.content_type, self.data, self.charset = status, content_type, data, charset
        self.truncated = truncated
        self.url = url  # the address that finally answered, after redirects


async def safe_fetch(*args, deadline: float | None = None, **kwargs) -> Fetched:
    """`_fetch` under an overall deadline (a trickling server cannot hold a request open)."""
    try:
        return await asyncio.wait_for(_fetch(*args, **kwargs), TOTAL_TIMEOUT if deadline is None else deadline)
    except asyncio.TimeoutError:
        raise FetchFailed("API request failed: timeout") from None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def _fetch(method: str, url: str, *, params=None, json_body=None, content=None,
                 auth_headers=None, auth_params=None, keep_host: str | None = None,
                 max_bytes: int = MAX_DOWNLOAD) -> Fetched:
    """Netguard-checked request with manual redirects, a hard download cap and DNS pinning.

    Every hop connects to an address that netguard validated: the URL host is rewritten to that IP, `Host` carries
    the original name and the TLS SNI/certificate check use it too, so a DNS-rebinding server cannot answer
    differently between the check and the connection. Auth is attached only to requests whose origin equals the
    original URL's origin. When `keep_host` is set, a redirect to any other host is refused. Raises ValueError for
    blocked URLs and FetchFailed otherwise.
    """
    origin = _origin(url)
    client = get_client()
    _, addresses = await resolve_public(url)
    for hop in range(MAX_REDIRECTS + 1):
        headers, merged = dict(_HEADERS), dict(params or {})
        if _origin(url) == origin:
            headers.update(auth_headers or {})
            merged.update(auth_params or {})
        try:
            orig = httpx.URL(url)
            target = orig.copy_merge_params(merged) if merged else orig
            headers["Host"] = orig.netloc.decode("ascii")
            headers["Connection"] = "close"  # never pool a connection keyed by IP: it could serve another name
            extensions = {} if _is_ip(orig.host) else {"sni_hostname": orig.host}
            kwargs = {"headers": headers, "follow_redirects": False, "extensions": extensions}
            if json_body is not None:
                kwargs["json"] = json_body
            elif content is not None:
                kwargs["content"] = content
        except (httpx.InvalidURL, UnicodeError):
            raise FetchFailed("API request failed: could not reach the server") from None
        nxt = status = None
        for attempt, address in enumerate(addresses[:MAX_ADDRESSES]):
            try:
                async with client.stream(method, target.copy_with(host=address), **kwargs) as resp:
                    location = resp.headers.get("location")
                    if resp.status_code in _REDIRECT_STATUSES and location:
                        nxt, status = urljoin(url, location), resp.status_code
                    else:
                        chunks, size = [], 0
                        async for chunk in resp.aiter_bytes():
                            chunks.append(chunk)
                            size += len(chunk)
                            if size >= max_bytes:
                                break
                        data = b"".join(chunks)[:max_bytes]
                        return Fetched(resp.status_code, resp.headers.get("content-type", ""), data,
                                       resp.charset_encoding or "utf-8", truncated=size >= max_bytes, url=url)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempt + 1 >= min(len(addresses), MAX_ADDRESSES):
                    raise FetchFailed("API request failed: could not reach the server") from None
            except httpx.TimeoutException:
                raise FetchFailed("API request failed: timeout") from None
            except (httpx.HTTPError, httpx.InvalidURL, UnicodeError):
                raise FetchFailed("API request failed: could not reach the server") from None
        # redirect: validate (and pin) the next hop, then loop
        if hop == MAX_REDIRECTS:
            raise FetchFailed("API request failed: too many redirects")
        try:
            _, addresses = await resolve_public(nxt)
        except ValueError:
            raise FetchFailed("API request failed: redirect to a blocked address refused") from None
        if keep_host is not None and (urlsplit(nxt).hostname or "").lower() != keep_host:
            raise FetchFailed("API request failed: redirect to a different host refused")
        if status in (301, 302, 303) and method not in ("GET", "HEAD"):
            method, json_body, content = "GET", None, None
        url, params = nxt, None
    raise FetchFailed("API request failed: too many redirects")  # pragma: no cover
