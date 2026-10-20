import socket

import pytest

from minty_core.netguard import check_public_url, resolve_public

PUBLIC = "93.184.216.34"


@pytest.fixture
def dns(monkeypatch):
    table = {"example.com": [PUBLIC], "rebind.example": ["10.1.2.3"], "mixed.example": [PUBLIC, "192.168.0.9"]}

    def fake(host, port, *a, **kw):
        if host not in table:
            if host.replace(".", "").isdigit() or ":" in host:
                return [(socket.AF_INET, 0, 0, "", (host, port))]
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, 0, 0, "", (addr, port)) for addr in table[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return table


async def test_public_host_allowed(dns):
    assert await check_public_url("https://Example.com./path?x=1") == "example.com"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1", "http://10.0.0.5", "http://[::1]", "http://169.254.169.254",
    "http://homeassistant.local:8123", "http://localhost:7777", "http://printer.lan",
    "file:///etc/passwd", "ftp://example.com", "http://user:pw@example.com", "http://100.64.0.1",
    "http://[::ffff:127.0.0.1]", "http://rebind.example", "http://mixed.example", "http://",
    "http://nonexistent.example", "http://[64:ff9b::7f00:1]", "http://[64:ff9b::a00:5]", "http://[::8.8.8.8]",
    "http://[::7f00:1]", "http://[2002:7f00:1::1]",
])
async def test_rejects(dns, url):
    with pytest.raises(ValueError):
        await check_public_url(url)


async def test_nat64_with_public_embedded_ipv4_is_allowed(dns):
    assert await check_public_url("http://[64:ff9b::808:808]/") == "64:ff9b::808:808"


async def test_resolve_public_returns_the_validated_addresses(dns):
    dns["multi.example"] = [PUBLIC, "93.184.216.35", PUBLIC]
    assert await resolve_public("https://Example.com./x") == ("example.com", [PUBLIC])
    assert await resolve_public("https://multi.example/") == ("multi.example", [PUBLIC, "93.184.216.35"])
    with pytest.raises(ValueError):
        await resolve_public("http://mixed.example")
