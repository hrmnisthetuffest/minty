import json
import socket
import stat

import httpx
import pytest

from tests.plugin_helpers import make_host, mock_http

PUBLIC = "93.184.216.34"
HOSTS = {"api.example.com": PUBLIC, "evil.example": "93.184.216.99", "docs.example.com": PUBLIC,
         "internal.example": "10.0.0.5"}
TAG = "[untrusted web content]"
KEY = "sk-SECRET-1234"


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    def fake(host, port, *a, **kw):
        if host not in HOSTS:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, 0, 0, "", (HOSTS[host], port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def registry(tmp_path, **entries):
    data = {}
    for name, entry in entries.items():
        data[name] = {"base_url": "https://api.example.com/v1", "auth": "none", "auth_name": "", "notes": "", "free": True}
        data[name].update(entry)
    (tmp_path / "apis.json").write_text(json.dumps(data))


def host_with(tmp_path, monkeypatch, handler, secrets=None, **entries):
    registry(tmp_path, **entries)
    seen = []

    def wrapped(req):
        seen.append(req)
        return handler(req)

    mock_http(monkeypatch, wrapped)
    return make_host(tmp_path, secrets=secrets), seen


def ok_json(req):
    return httpx.Response(200, json={"hello": "world"})


# ---- apis_call ----

async def test_call_builds_url_and_prefixes_output(tmp_path, monkeypatch):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, demo={})
    out = await host.call("apis_call", {"name": "demo", "path": "/things", "params": {"q": "a b", "n": 2}})
    assert out.startswith(TAG)
    assert '"hello"' in out
    assert seen[0].url.path == "/v1/things" and seen[0].headers["host"] == "api.example.com"
    assert seen[0].url.params["q"] == "a b" and seen[0].url.params["n"] == "2"
    assert "authorization" not in seen[0].headers


async def test_call_unregistered_name(tmp_path, monkeypatch):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, demo={})
    out = await host.call("apis_call", {"name": "ghost", "path": "/x"})
    assert "not registered" in out and not seen


@pytest.mark.parametrize("path", [
    "//evil.example/x", "@evil.example", "/@evil.example", "https://evil.example/x", "/x://y", "x", "",
    "/a\\b", "/a\r\nHost: evil", "/a b\nc", "/x?k=v", "/x#frag", "/a@b",
    "/a/../b", "/./x", "/a/..", "/a/%2e%2e/b", "/%2E/x", "/a/.%2e/x", "/a/%2E%2e",
])
async def test_call_rejects_path_tricks(tmp_path, monkeypatch, path):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, demo={})
    out = await host.call("apis_call", {"name": "demo", "path": path})
    assert out.startswith("Error:") and not seen


async def test_call_non_string_path_and_params(tmp_path, monkeypatch):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, demo={})
    assert (await host.call("apis_call", {"name": "demo", "path": 5})).startswith("Error:")
    assert (await host.call("apis_call", {"name": "demo", "path": "/x", "params": "q=1"})).startswith("Error:")
    assert not seen


async def test_auth_bearer_header_and_query_never_in_output(tmp_path, monkeypatch):
    def echo(req):
        return httpx.Response(200, text=f"you sent {req.headers.get('authorization')} {req.headers.get('x-api-key')} "
                                        f"{req.url.params.get('apikey')}")

    host, seen = host_with(
        tmp_path, monkeypatch, echo, secrets={"plugin.apis.b": KEY, "plugin.apis.h": KEY, "plugin.apis.q": KEY},
        b={"auth": "bearer", "free": False},
        h={"auth": "header", "auth_name": "X-Api-Key", "free": False},
        q={"auth": "query", "auth_name": "apikey", "free": False},
    )
    outs = [await host.call("apis_call", {"name": n, "path": "/x"}) for n in ("b", "h", "q")]
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert seen[1].headers["x-api-key"] == KEY
    assert seen[2].url.params["apikey"] == KEY
    for out in outs:
        assert KEY not in out
        assert out.startswith(TAG)


async def test_missing_key_message_has_no_secret(tmp_path, monkeypatch):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, keyed={"auth": "bearer", "free": False})
    out = await host.call("apis_call", {"name": "keyed", "path": "/x"})
    assert "plugin.apis.keyed" in out and not seen


async def test_http_error_is_clean_and_redacted(tmp_path, monkeypatch):
    def fail(req):
        return httpx.Response(401, text=f"bad key {KEY}")

    host, _ = host_with(tmp_path, monkeypatch, fail, secrets={"plugin.apis.q": KEY},
                        q={"auth": "query", "auth_name": "apikey", "free": False})
    out = await host.call("apis_call", {"name": "q", "path": "/x"})
    assert "HTTP 401" in out and KEY not in out and "apikey=" not in out


async def test_transport_error_is_clean(tmp_path, monkeypatch):
    def boom(req):
        raise httpx.ConnectError(f"cannot connect to {req.url}")

    host, _ = host_with(tmp_path, monkeypatch, boom, secrets={"plugin.apis.q": KEY},
                        q={"auth": "query", "auth_name": "apikey", "free": False})
    out = await host.call("apis_call", {"name": "q", "path": "/x"})
    assert out.startswith("API request failed") and KEY not in out and "example.com" not in out


async def test_timeout_message(tmp_path, monkeypatch):
    def slow(req):
        raise httpx.ReadTimeout("slow")

    host, _ = host_with(tmp_path, monkeypatch, slow, demo={})
    assert (await host.call("apis_call", {"name": "demo", "path": "/x"})) == "API request failed: timeout"


async def test_total_deadline(tmp_path, monkeypatch):
    import asyncio

    async def slow(req):
        await asyncio.sleep(5)
        return httpx.Response(200, text="late")

    host, _ = host_with(tmp_path, monkeypatch, slow, demo={})
    import minty_core.safefetch as safefetch
    monkeypatch.setattr(safefetch, "TOTAL_TIMEOUT", 0.05)
    assert (await host.call("apis_call", {"name": "demo", "path": "/x"})) == "API request failed: timeout"


async def test_key_api_over_plain_http_refused(tmp_path, monkeypatch):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, secrets={"plugin.apis.k": KEY},
                           k={"base_url": "http://api.example.com", "auth": "bearer", "free": False})
    out = await host.call("apis_call", {"name": "k", "path": "/x"})
    assert out.startswith("Error:") and KEY not in out and not seen
    reg = await host.call("apis_register", {"name": "k2", "base_url": "http://api.example.com", "auth": "query"})
    assert reg.startswith("Error:")


async def test_response_cap_and_download_cap(tmp_path, monkeypatch):
    host, _ = host_with(tmp_path, monkeypatch, lambda r: httpx.Response(200, text="x" * 3_000_000), demo={})
    out = await host.call("apis_call", {"name": "demo", "path": "/big"})
    assert len(out) <= len(TAG) + 1 + 6000 + 40


async def test_binary_content(tmp_path, monkeypatch):
    def png(req):
        return httpx.Response(200, content=b"\x89PNG" + b"\0" * 96, headers={"content-type": "image/png"})

    host, _ = host_with(tmp_path, monkeypatch, png, demo={})
    out = await host.call("apis_call", {"name": "demo", "path": "/img"})
    assert out == f"{TAG}\n[binary content, 100 bytes]"


# ---- redirects ----

async def test_redirect_same_host_is_followed_and_auth_kept(tmp_path, monkeypatch):
    def handler(req):
        if req.url.path == "/v1/old":
            return httpx.Response(302, headers={"location": "/v1/new"})
        return httpx.Response(200, text=f"landed {req.headers.get('authorization')}")

    host, seen = host_with(tmp_path, monkeypatch, handler, secrets={"plugin.apis.b": KEY},
                           b={"auth": "bearer", "free": False})
    out = await host.call("apis_call", {"name": "b", "path": "/old"})
    assert "landed" in out and KEY not in out
    assert [r.url.path for r in seen] == ["/v1/old", "/v1/new"]
    assert seen[1].headers["authorization"] == f"Bearer {KEY}"


async def test_call_redirect_to_other_host_refused_without_auth_leak(tmp_path, monkeypatch):
    def handler(req):
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    host, seen = host_with(tmp_path, monkeypatch, handler, secrets={"plugin.apis.b": KEY},
                           b={"auth": "bearer", "free": False})
    out = await host.call("apis_call", {"name": "b", "path": "/old"})
    assert "redirect" in out.lower() and len(seen) == 1


async def test_redirect_to_private_address_refused(tmp_path, monkeypatch):
    def handler(req):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    host, seen = host_with(tmp_path, monkeypatch, handler, demo={})
    out = await host.call("apis_read_url", {"url": "https://docs.example.com/page"})
    assert "redirect" in out.lower() and len(seen) == 1


async def test_redirect_limit(tmp_path, monkeypatch):
    def handler(req):
        return httpx.Response(302, headers={"location": "/v1/loop"})

    host, seen = host_with(tmp_path, monkeypatch, handler, demo={})
    out = await host.call("apis_call", {"name": "demo", "path": "/loop"})
    assert "too many redirects" in out.lower() and len(seen) == 4


async def test_registered_base_resolving_private_is_refused_at_call(tmp_path, monkeypatch):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, bad={"base_url": "https://internal.example"})
    out = await host.call("apis_call", {"name": "bad", "path": "/x"})
    assert out.startswith("Error:") and not seen


# ---- apis_read_url ----

async def test_read_url_strips_html_and_prefixes(tmp_path, monkeypatch):
    page = "<html><head><style>p{}</style><script>alert(1)</script></head><body><h1>Docs</h1><p>Use &amp; enjoy</p></body></html>"

    def handler(req):
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

    host, seen = host_with(tmp_path, monkeypatch, handler)
    out = await host.call("apis_read_url", {"url": "https://docs.example.com/page"})
    assert out == f"{TAG}\nDocs Use & enjoy"
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://internal.example/", "file:///etc/passwd",
                                 "http://user:pw@docs.example.com/", "http://[::1]/"])
async def test_read_url_rejects_private(tmp_path, monkeypatch, url):
    host, seen = host_with(tmp_path, monkeypatch, ok_json)
    out = await host.call("apis_read_url", {"url": url})
    assert out.startswith("Error:") and not seen


# ---- apis_send / register / unregister ----

async def test_send_posts_json_with_auth_and_is_risky(tmp_path, monkeypatch):
    def handler(req):
        return httpx.Response(201, json={"id": 7})

    host, seen = host_with(tmp_path, monkeypatch, handler, secrets={"plugin.apis.b": KEY},
                           b={"auth": "bearer", "free": False})
    tool = host.get_tool("apis_send")
    assert tool.risky
    assert tool.describe({"name": "b", "method": "post", "path": "/items"}) == "Should I send POST to b/items?"
    out = await host.call("apis_send", {"name": "b", "method": "post", "path": "/items", "body": {"a": 1}})
    assert seen[0].method == "POST" and json.loads(seen[0].content) == {"a": 1}
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert '"id"' in out and KEY not in out


async def test_send_rejects_bad_method_and_bad_path(tmp_path, monkeypatch):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, demo={})
    assert (await host.call("apis_send", {"name": "demo", "method": "GET", "path": "/x"})).startswith("Error:")
    assert (await host.call("apis_send", {"name": "demo", "method": "POST", "path": "//evil.example"})).startswith("Error:")
    assert not seen


async def test_send_post_redirect_to_other_host_not_followed(tmp_path, monkeypatch):
    def handler(req):
        return httpx.Response(307, headers={"location": "https://evil.example/collect"})

    host, seen = host_with(tmp_path, monkeypatch, handler, demo={})
    out = await host.call("apis_send", {"name": "demo", "method": "POST", "path": "/x", "body": {"s": 1}})
    assert "redirect" in out.lower() and len(seen) == 1


async def test_register_is_risky_and_persists_0600(tmp_path, monkeypatch):
    host, _ = host_with(tmp_path, monkeypatch, ok_json)
    tool = host.get_tool("apis_register")
    assert tool.risky and tool.describe({"name": "x", "base_url": "https://api.example.com/v2"}) == \
        "Should I add the x API at api.example.com?"
    out = await host.call("apis_register", {"name": "cats", "base_url": "https://api.example.com/v2/",
                                            "notes": "cat facts", "auth": "header", "auth_name": "X-Key"})
    assert "plugin.apis.cats" in out
    path = tmp_path / "apis.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    entry = json.loads(path.read_text())["cats"]
    assert entry == {"base_url": "https://api.example.com/v2", "auth": "header", "auth_name": "X-Key",
                     "notes": "cat facts", "free": False}
    listing = await host.call("apis_list", {})
    assert "cats" in listing and "needs a key" in listing and "cat facts" in listing


@pytest.mark.parametrize("args", [
    {"name": "Bad Name", "base_url": "https://api.example.com"},
    {"name": "-x", "base_url": "https://api.example.com"},
    {"name": "ok\n", "base_url": "https://api.example.com"},
    {"name": "ok", "base_url": "http://10.0.0.5"},
    {"name": "ok", "base_url": "https://internal.example"},
    {"name": "ok", "base_url": "ftp://api.example.com"},
    {"name": "ok", "base_url": "https://user:pw@api.example.com"},
    {"name": "ok", "base_url": "https://api.example.com/?k=v"},
    {"name": "ok", "base_url": "https://api.example.com", "auth": "magic"},
    {"name": "ok", "base_url": "https://api.example.com", "auth": "header", "auth_name": "bad header\r\n"},
])
async def test_register_rejects_bad_input(tmp_path, monkeypatch, args):
    host, _ = host_with(tmp_path, monkeypatch, ok_json)
    out = await host.call("apis_register", args)
    assert out.startswith("Error:")
    assert not (tmp_path / "apis.json").exists() or json.loads((tmp_path / "apis.json").read_text()) == {}


async def test_unregister_removes_entry_and_key(tmp_path, monkeypatch):
    host, _ = host_with(tmp_path, monkeypatch, ok_json, secrets={"plugin.apis.demo": KEY}, demo={})
    tool = host.get_tool("apis_unregister")
    assert tool.risky and tool.describe({"name": "demo"}) == "Should I remove the demo API?"
    out = await host.call("apis_unregister", {"name": "demo"})
    assert "removed" in out.lower()
    assert json.loads((tmp_path / "apis.json").read_text()) == {}
    assert (await host.call("apis_unregister", {"name": "demo"})).startswith("No API")


async def test_list_never_shows_secrets_and_survives_corrupt_file(tmp_path, monkeypatch):
    host, _ = host_with(tmp_path, monkeypatch, ok_json, secrets={"plugin.apis.k": KEY},
                        k={"auth": "bearer", "free": False, "notes": "n"})
    out = await host.call("apis_list", {})
    assert "k" in out and KEY not in out
    (tmp_path / "apis.json").write_text("{not json")
    assert "No APIs registered" in await host.call("apis_list", {})
    (tmp_path / "apis.json").write_text(json.dumps([1, 2]))
    assert "No APIs registered" in await host.call("apis_list", {})
    (tmp_path / "apis.json").write_text(json.dumps({"good": {"base_url": "https://api.example.com"}, "bad": 5}))
    out = await host.call("apis_list", {})
    assert "good" in out and "- bad" not in out


# ---- catalog ----

async def test_catalog_search_flags_free_and_keyed(tmp_path, monkeypatch):
    host, _ = host_with(tmp_path, monkeypatch, ok_json)
    out = await host.call("apis_search_catalog", {"query": "weather"})
    assert out.startswith(TAG)
    lines = out.splitlines()[1:]
    assert 1 <= len(lines) <= 8
    assert any("Auth: none → free, no key" in line for line in lines)
    out2 = await host.call("apis_search_catalog", {"query": "zzzqqq nonexistent"})
    assert "No catalog matches" in out2


async def test_catalog_contains_well_known_free_apis():
    from pathlib import Path

    import minty_core.plugins.builtin.apis as pkg

    entries = json.loads((Path(pkg.__file__).parent / "catalog.json").read_text(encoding="utf-8"))
    assert len(entries) > 500
    assert {"name", "link", "description", "auth", "https", "category"} <= set(entries[0])
    free = {e["name"] for e in entries if e["auth"] == "none"}
    assert "Open-Meteo" in free
    assert not any(e["auth"] in ("", "No") for e in entries)


# ---- DNS pinning ----

def _resolver(monkeypatch, *answers):
    """getaddrinfo that gives each successive lookup of a host the next answer (a rebinding server)."""
    calls = {}

    def fake(host, port, *a, **kw):
        n = calls.get(host, 0)
        calls[host] = n + 1
        addrs = answers[min(n, len(answers) - 1)]
        return [(socket.AF_INET, 0, 0, "", (addr, port)) for addr in addrs]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return calls


async def test_connection_is_pinned_to_the_validated_ip_not_re_resolved(tmp_path, monkeypatch):
    calls = _resolver(monkeypatch, [PUBLIC], ["10.0.0.5"])  # second lookup would rebind to a private address
    host, seen = host_with(tmp_path, monkeypatch, ok_json, demo={"base_url": "https://api.example.com:8443/v1"})
    out = await host.call("apis_call", {"name": "demo", "path": "/things", "params": {"q": "1"}})
    assert out.startswith(TAG)
    req = seen[0]
    assert req.url.host == PUBLIC and req.url.port == 8443 and req.url.path == "/v1/things"
    assert req.url.params["q"] == "1"
    assert req.headers["host"] == "api.example.com:8443"
    assert req.extensions["sni_hostname"] == "api.example.com"
    assert req.headers["connection"] == "close"  # a pooled connection is never reused for another name
    assert calls == {"api.example.com": 1}


async def test_default_port_host_header_has_no_port(tmp_path, monkeypatch):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, demo={})
    await host.call("apis_call", {"name": "demo", "path": "/x"})
    assert seen[0].headers["host"] == "api.example.com" and seen[0].url.host == PUBLIC


async def test_ipv6_address_is_bracketed(tmp_path, monkeypatch):
    _resolver(monkeypatch, ["2606:4700::1111"])
    host, seen = host_with(tmp_path, monkeypatch, ok_json, demo={})
    await host.call("apis_call", {"name": "demo", "path": "/x"})
    assert str(seen[0].url).startswith("https://[2606:4700::1111]/v1/x")
    assert seen[0].headers["host"] == "api.example.com"


async def test_each_redirect_hop_is_pinned_to_its_own_validated_ip_and_gets_no_auth(tmp_path, monkeypatch):
    def handler(req):
        if req.headers["host"] == "docs.example.com":
            return httpx.Response(302, headers={"location": "https://evil.example/landing"})
        return httpx.Response(200, text="landed")

    host, seen = host_with(tmp_path, monkeypatch, handler)
    out = await host.call("apis_read_url", {"url": "https://docs.example.com/start"})
    assert out == f"{TAG}\nlanded"
    assert [(r.url.host, r.headers["host"], r.extensions["sni_hostname"]) for r in seen] == [
        (PUBLIC, "docs.example.com", "docs.example.com"), ("93.184.216.99", "evil.example", "evil.example")]


async def test_falls_back_to_the_next_validated_address_on_connect_failure(tmp_path, monkeypatch):
    _resolver(monkeypatch, [PUBLIC, "93.184.216.35"])

    def handler(req):
        if req.url.host == PUBLIC:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(200, text="second")

    host, seen = host_with(tmp_path, monkeypatch, handler, demo={})
    out = await host.call("apis_call", {"name": "demo", "path": "/x"})
    assert out.endswith("second") and [r.url.host for r in seen] == [PUBLIC, "93.184.216.35"]


async def test_requests_ask_for_identity_encoding(tmp_path, monkeypatch):
    host, seen = host_with(tmp_path, monkeypatch, ok_json, demo={})
    await host.call("apis_call", {"name": "demo", "path": "/x"})
    assert seen[0].headers["accept-encoding"] == "identity"


async def test_real_connection_goes_to_the_pinned_ip_with_the_original_host_header(tmp_path, monkeypatch):
    """No mock transport: a real httpx client connects to the pinned address, never resolving the name."""
    import asyncio

    import minty_core.plugins.builtin.apis.plugin as apis_plugin

    got = []

    async def serve(reader, writer):
        got.append((await reader.readuntil(b"\r\n\r\n")).decode("latin-1"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 6\r\nConnection: close\r\n\r\npinned")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def fake_resolve(url):
        return "pinned.invalid", ["127.0.0.1"]

    import minty_core.safefetch as safefetch
    monkeypatch.setattr(safefetch, "resolve_public", fake_resolve)
    client = httpx.AsyncClient(trust_env=False)
    monkeypatch.setattr("minty_core.httpclient._client", client)
    try:
        fetched = await apis_plugin.safe_fetch("GET", f"http://pinned.invalid:{port}/hello?x=1")
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()
    assert fetched.status == 200 and fetched.data == b"pinned"
    head = got[0].lower()
    assert head.startswith("get /hello?x=1 http/1.1") and f"host: pinned.invalid:{port}" in head


# ---- re-registration must not hand a stored key to a new host ----

def _stored(tmp_path, name="demo"):
    from minty_core.keystore import SecretsStore

    return SecretsStore(tmp_path).get(f"plugin.apis.{name}")


async def test_reregistering_with_a_new_host_deletes_the_stored_key(tmp_path, monkeypatch):
    host, _ = host_with(tmp_path, monkeypatch, ok_json, secrets={"plugin.apis.demo": KEY, "plugin.apis.other": KEY},
                        demo={"auth": "bearer", "free": False}, other={"auth": "bearer", "free": False})
    out = await host.call("apis_register", {"name": "demo", "base_url": "https://docs.example.com", "auth": "bearer"})
    assert "Updated" in out
    assert _stored(tmp_path) is None and _stored(tmp_path, "other") == KEY


@pytest.mark.parametrize("changed", [
    {"base_url": "http://api.example.com", "auth": "none"},       # scheme (no key over http)
    {"base_url": "https://api.example.com:8443/v1"},               # port
    {"auth": "query"},                                              # auth kind
    {"auth_name": "X-Other"},                                       # header/param name
])
async def test_reregistering_with_any_changed_target_part_deletes_the_key(tmp_path, monkeypatch, changed):
    host, _ = host_with(tmp_path, monkeypatch, ok_json, secrets={"plugin.apis.demo": KEY},
                        demo={"auth": "header", "auth_name": "X-Key", "free": False})
    args = {"name": "demo", "base_url": "https://api.example.com/v1", "auth": "header", "auth_name": "X-Key"}
    args.update(changed)
    assert "Updated" in await host.call("apis_register", args)
    assert _stored(tmp_path) is None


async def test_reregistering_the_same_target_keeps_the_key(tmp_path, monkeypatch):
    host, _ = host_with(tmp_path, monkeypatch, ok_json, secrets={"plugin.apis.demo": KEY},
                        demo={"auth": "header", "auth_name": "X-Key", "free": False})
    out = await host.call("apis_register", {"name": "demo", "base_url": "https://API.example.com/v2/", "notes": "n",
                                            "auth": "header", "auth_name": "X-Key"})
    assert "Updated" in out and _stored(tmp_path) == KEY


async def test_register_prompt_warns_when_a_replacement_drops_the_key(tmp_path, monkeypatch):
    host, _ = host_with(tmp_path, monkeypatch, ok_json, demo={"auth": "bearer", "free": False})
    describe = host.get_tool("apis_register").describe
    assert describe({"name": "demo", "base_url": "https://evil.example/x", "auth": "bearer"}) == \
        "Should I replace the demo API with evil.example? Its saved key will be removed."
    assert describe({"name": "demo", "base_url": "https://api.example.com/other", "auth": "bearer"}) == \
        "Should I add the demo API at api.example.com?"
    assert describe({"name": "fresh", "base_url": "https://evil.example"}) == \
        "Should I add the fresh API at evil.example?"
    assert describe({"name": 5, "base_url": None}).startswith("Should I add the")
