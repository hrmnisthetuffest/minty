"""One check for an address that may be fetched on your behalf: an ordinary public web address."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def check_web_url(url: str) -> str:
    url = str(url or "").strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("Give an ordinary web address starting with http or https.")
    if parts.username or parts.password:
        raise ValueError("Addresses with a name and password inside are not allowed.")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("That address is on your own network, not the web.")
    except ValueError as e:
        if "own network" in str(e):
            raise
        if "." not in host:
            raise ValueError("That is not a public web address.") from None
    return url
