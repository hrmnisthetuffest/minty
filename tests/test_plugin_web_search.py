import httpx

from tests.plugin_helpers import make_host, mock_http

DDG_HTML = """
<div class="result"><h2 class="result__title"><a rel="nofollow" class="result__a"
 href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpage&amp;rut=abc">Example <b>Title</b></a></h2>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpage">A <b>snippet</b> here &amp; there.</a></div>
<div class="result"><h2 class="result__title"><a rel="nofollow" class="result__a"
 href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fother.net%2F&amp;rut=def">Second</a></h2>
<a class="result__snippet" href="x">Second snippet.</a></div>
"""


async def test_duckduckgo_fallback_without_key(tmp_path, monkeypatch):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, text=DDG_HTML)

    mock_http(monkeypatch, handler)
    out = await make_host(tmp_path).call("web_search_search", {"query": "example"})
    assert seen[0].url.host == "html.duckduckgo.com" and seen[0].url.params["q"] == "example"
    assert out.splitlines() == [
        "1. Example Title: A snippet here & there. (example.org)",
        "2. Second: Second snippet. (other.net)",
    ]


async def test_brave_when_key_present(tmp_path, monkeypatch):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"web": {"results": [
            {"title": "A", "url": "https://a.test/x", "description": "<strong>bold</strong> desc"}]}})

    mock_http(monkeypatch, handler)
    host = make_host(tmp_path, secrets={"plugin.web_search.brave_key": "BSA-key-123456"})
    out = await host.call("web_search_search", {"query": "cats"})
    assert seen[0].headers["x-subscription-token"] == "BSA-key-123456"
    assert out == "1. A: bold desc (a.test)"


async def test_blocked_or_empty_search_is_a_clean_message(tmp_path, monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(202, text="challenge"))
    assert await make_host(tmp_path).call("web_search_search", {"query": "x"}) == "Search is unavailable right now."
    mock_http(monkeypatch, lambda req: httpx.Response(200, text="<html></html>"))
    assert await make_host(tmp_path).call("web_search_search", {"query": "x"}) == "I found no results."


async def test_query_validation(tmp_path, monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, text=""))
    host = make_host(tmp_path)
    assert (await host.call("web_search_search", {"query": "  "})).startswith("Error: ValueError")
    assert (await host.call("web_search_search", {"query": "x" * 500})).startswith("Error: ValueError")


async def test_unbounded_text_truncation(tmp_path, monkeypatch):
    """Issue 1: Unbounded untrusted text. Truncate to 200 chars with ellipsis."""
    long_snippet = "x" * 5000
    html = f"""
    <div class="result"><h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2F&amp;rut=abc">{long_snippet}</a></h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2F">{long_snippet}</a></div>
    """
    mock_http(monkeypatch, lambda req: httpx.Response(200, text=html))
    out = await make_host(tmp_path).call("web_search_search", {"query": "test"})
    # Result line should be under 450 characters total
    assert len(out.splitlines()[0]) < 450, f"Line too long: {len(out.splitlines()[0])}"
    # Should contain ellipsis for truncated text
    assert "…" in out


async def test_brave_fragile_parsing(tmp_path, monkeypatch):
    """Issue 2: Fragile Brave parsing. Handle missing fields and HTML in title."""
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"web": {"results": [
            {"title": "<strong>Bold</strong> Title", "url": "https://a.test/x", "description": "desc 1"},
            {"url": "https://b.test/y", "description": "desc 2"},  # missing title
            {"title": "C", "description": "desc 3"},  # missing url - should skip
        ]}})

    mock_http(monkeypatch, handler)
    host = make_host(tmp_path, secrets={"plugin.web_search.brave_key": "key"})
    out = await host.call("web_search_search", {"query": "test"})
    # Should not error, should have 2 results
    assert not out.startswith("Error:"), f"Got error: {out}"
    lines = out.splitlines()
    assert len(lines) == 2
    assert "Bold Title" in lines[0]  # HTML should be cleaned from title
    assert ": desc 2" in lines[1]  # Missing title should show as ": desc"


async def test_clean_error_messages(tmp_path, monkeypatch):
    """Issue 3: Errors should return clean message, not expose key or traceback."""
    # Test Brave 500 error
    mock_http(monkeypatch, lambda req: httpx.Response(500, text="Internal Server Error"))
    host = make_host(tmp_path, secrets={"plugin.web_search.brave_key": "secret-key-12345"})
    out = await host.call("web_search_search", {"query": "test"})
    assert out == "Search is unavailable right now."
    assert "secret-key" not in out and "Error:" not in out

    # Test DDG connection error
    def bad_handler(req):
        raise httpx.ConnectError("Connection failed")

    mock_http(monkeypatch, bad_handler)
    host = make_host(tmp_path)
    out = await host.call("web_search_search", {"query": "test"})
    assert out == "Search is unavailable right now."
    assert "ConnectError" not in out and "Error:" not in out


async def test_ddg_mispairing_prevention(tmp_path, monkeypatch):
    """Issue 4: DDG result mis-pairing. Result A without snippet shouldn't get result B's snippet."""
    html = """
    <div class="result"><h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.test%2F&amp;rut=a">A Title</a></h2>
    </div>
    <div class="result"><h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fb.test%2F&amp;rut=b">B Title</a></h2>
    <a class="result__snippet" href="x">B snippet content here</a></div>
    """
    mock_http(monkeypatch, lambda req: httpx.Response(200, text=html))
    out = await make_host(tmp_path).call("web_search_search", {"query": "test"})
    lines = out.splitlines()
    # Result A should exist but not contain "B snippet"
    assert "A Title" in lines[0]
    assert "B snippet" not in lines[0], "Result A should not contain Result B's snippet"
    # Result B should have its snippet
    assert "B Title" in lines[1]
    assert "B snippet" in lines[1]


async def test_real_duckduckgo_multiclass_markup(tmp_path, monkeypatch):
    """Fix 1: Real DDG uses multi-class containers, not just <div class="result">."""
    html = """
    <div class="result results_links results_links_deep web-result "><h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freal1.test%2F&amp;rut=a">Real Title 1</a></h2>
    <a class="result__snippet" href="x">Real snippet 1 here.</a></div>
    <div class="result results_links results_links_deep web-result "><h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freal2.test%2F&amp;rut=b">Real Title 2</a></h2>
    <a class="result__snippet" href="x">Real snippet 2 text.</a></div>
    """
    mock_http(monkeypatch, lambda req: httpx.Response(200, text=html))
    out = await make_host(tmp_path).call("web_search_search", {"query": "test"})
    lines = out.splitlines()
    assert len(lines) == 2, f"Expected 2 results, got {len(out)}"
    assert "Real Title 1" in lines[0]
    assert "Real snippet 1" in lines[0]
    assert "Real Title 2" in lines[1]
    assert "Real snippet 2" in lines[1]


async def test_brave_non_json_200(tmp_path, monkeypatch):
    """Fix 2: Brave returns 200 but response is not valid JSON."""
    mock_http(monkeypatch, lambda req: httpx.Response(200, text="<html>not json</html>"))
    host = make_host(tmp_path, secrets={"plugin.web_search.brave_key": "key"})
    out = await host.call("web_search_search", {"query": "test"})
    assert out == "Search is unavailable right now."
    assert "Error:" not in out


async def test_brave_null_title(tmp_path, monkeypatch):
    """Fix 3: Brave result with null title should not crash."""
    def handler(req):
        return httpx.Response(200, json={"web": {"results": [
            {"title": None, "url": "https://null-title.test/", "description": "A result with no title"},
            {"title": "Good Title", "url": "https://good.test/", "description": "Normal result"},
        ]}})

    mock_http(monkeypatch, handler)
    host = make_host(tmp_path, secrets={"plugin.web_search.brave_key": "key"})
    out = await host.call("web_search_search", {"query": "test"})
    assert not out.startswith("Error:"), f"Got error: {out}"
    lines = out.splitlines()
    assert len(lines) == 2
    # First result should have empty title but valid description
    assert ": A result with no title" in lines[0]
    assert "Good Title" in lines[1]
