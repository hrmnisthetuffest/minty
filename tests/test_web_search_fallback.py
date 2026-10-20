import httpx
import pytest

from minty_core.web.search import is_challenge, wikipedia_results
from tests.plugin_helpers import make_host, mock_http

WIKI = {"query": {"search": [
    {"title": "Moon landing", "snippet": 'The <span class="searchmatch">Moon</span> landing &amp; more'},
    {"title": "Apollo 11", "snippet": "First crewed landing."}]}}


@pytest.mark.parametrize("status,text,expected", [
    (202, "", True), (429, "slow down", True), (200, "<div id='anomaly-modal'>", True),
    (200, "Unfortunately, bots use DuckDuckGo too.", True), (200, "https://www.google.com/sorry/index?x", True),
    (200, "<a class='result__a'>real result</a>", False),
    (200, "An article about how CAPTCHA tests work and why they exist", False)])
def test_challenge_detection(status, text, expected):
    assert is_challenge(status, text) is expected


def test_wikipedia_results_are_cleaned():
    rows = wikipedia_results(WIKI)
    assert rows[0] == {"title": "Moon landing", "url": "https://en.wikipedia.org/wiki/Moon_landing",
                       "snippet": "The Moon landing & more"}
    assert wikipedia_results({}) == []


def host(tmp_path, helpers=True):
    h = make_host(tmp_path)
    data = h._store.config.model_dump(mode="json")
    data["web"]["helpers"] = helpers
    h._store.update(data)
    return h


def router(ddg_status=202, ddg_text="challenge", wiki_ok=True):
    seen = []

    def handler(req):
        seen.append(req.url.host)
        if req.url.host == "html.duckduckgo.com":
            return httpx.Response(ddg_status, text=ddg_text)
        return httpx.Response(200, json=WIKI) if wiki_ok else httpx.Response(500)

    return handler, seen


async def test_a_bot_check_falls_back_to_wikipedia_and_says_so(tmp_path, monkeypatch):
    handler, seen = router()
    mock_http(monkeypatch, handler)
    out = await host(tmp_path).call("web_search_search", {"query": "moon landing"})
    assert seen == ["html.duckduckgo.com", "en.wikipedia.org"]
    assert out.startswith("The search engine asked for a CAPTCHA, so these come from Wikipedia.")
    assert "1. Moon landing: The Moon landing & more (en.wikipedia.org)" in out


async def test_a_challenge_page_with_status_200_also_falls_back(tmp_path, monkeypatch):
    handler, _seen = router(200, "<div id='anomaly-modal'>Unfortunately, bots use DuckDuckGo too.</div>")
    mock_http(monkeypatch, handler)
    assert "Wikipedia" in await host(tmp_path).call("web_search_search", {"query": "moon"})


async def test_normal_results_never_touch_wikipedia(tmp_path, monkeypatch):
    handler, seen = router(200, '<a class="result__a" href="https://a.example/x">Hit</a>'
                                '<a class="result__snippet" href="x">Snip</a>')
    mock_http(monkeypatch, handler)
    out = await host(tmp_path).call("web_search_search", {"query": "moon"})
    assert seen == ["html.duckduckgo.com"] and "Hit" in out


async def test_with_the_switch_off_nothing_changes(tmp_path, monkeypatch):
    handler, seen = router()
    mock_http(monkeypatch, handler)
    assert await host(tmp_path, helpers=False).call("web_search_search", {"query": "moon"}) == \
        "Search is unavailable right now."
    assert seen == ["html.duckduckgo.com"]


async def test_if_the_fallback_fails_too_the_message_is_the_plain_one(tmp_path, monkeypatch):
    handler, _seen = router(wiki_ok=False)
    mock_http(monkeypatch, handler)
    assert await host(tmp_path).call("web_search_search", {"query": "moon"}) == "Search is unavailable right now."
