"""SSRF guard: only http(s) URLs that resolve exclusively to public internet addresses pass.

`resolve_public` returns the validated addresses so a caller can connect to one of them directly (IP pinning);
a caller that lets the HTTP client resolve the name again re-opens a DNS-rebinding window (TOCTOU).
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

BLOCKED_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "homeassistant.local"}


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_V4_COMPATIBLE = ipaddress.ip_network("::/96")


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _V4_COMPATIBLE:  # deprecated IPv4-compatible form, also covers :: and ::1
            return False
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(ip.packed[-4:])
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
    return ip.is_global


async def resolve_public(url: str) -> tuple[str, list[str]]:
    """Return (hostname, validated address strings, in resolver order) for a public http(s) URL.

    Raises ValueError unless the scheme is http(s) and every resolved address is public.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("only http and https URLs are allowed")
    if parts.username or parts.password:
        raise ValueError("credentials in URLs are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or host in BLOCKED_NAMES or host.endswith((".local", ".internal", ".lan", ".home")):
        raise ValueError("local or internal hosts are not allowed")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port)
    except OSError:
        raise ValueError("could not resolve that host") from None
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses or not all(_is_public(a) for a in addresses):
        raise ValueError("that address is private or local")
    return host, addresses


async def check_public_url(url: str) -> str:
    """Return the hostname if url is http(s) to public internet addresses only; else raise ValueError."""
    return (await resolve_public(url))[0]
