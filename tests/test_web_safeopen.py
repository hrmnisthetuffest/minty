"""Safe-open: look at a link before anyone visits it. No cookies in or out, no scripts, redirects shown."""
import httpx
import pytest

from minty_core.safefetch import Fetched
from minty_core.web.safeopen import fetch_clean, inspect_url, safe_open


@pytest.mark.parametrize("url,verdict", [
    ("https://www.wikipedia.org/wiki/Moon", "ok"),
    ("https://docs.python.org/3/", "ok"),
    ("https://paypal.com@evil.example/login", "danger"),
    ("https://paypa1-secure.com/verify", "danger"),
    ("https://g00gle.com/", "danger"),
    ("https://amazon.in.account-check.top/", "danger"),
    ("https://www.amazon.in/dp/B0", "ok"),
    ("http://127.0.0.1:8080/admin", "danger"),
    ("http://192.168.1.1/", "danger"),
    ("https://bit.ly/3abcde", "caution"),
    ("http://example.org/page", "caution"),
    ("https://xn--pypal-4ve.com/", "caution"),
    ("https://91.121.5.4/login", "caution"),
    ("javascript:alert(1)", "danger"),
    ("file:///etc/passwd", "danger"),
])
def test_link_verdicts(url, verdict):
    assert inspect_url(url).verdict == verdict


def test_a_verdict_comes_with_plain_reasons():
    r = inspect_url("https://paypal.com@evil.example/login")
    assert "evil.example" in " ".join(r.reasons) and r.host == "evil.example"
    assert "paypal" in " ".join(inspect_url("https://paypa1-secure.com/").reasons).lower()


def fetched(body, ctype="text/html; charset=utf-8", url="", status=200):
    f = Fetched(status, ctype, body.encode(), "utf-8")
    f.url = url
    return f


async def test_a_good_link_returns_title_and_text_without_scripts():
    async def fetch(url):
        return fetched("<title>Moon facts</title><script>steal()</script><p>The Moon orbits Earth.</p>", url=url)

    out = await safe_open("https://www.wikipedia.org/wiki/Moon", fetch)
    assert out.opened and out.title == "Moon facts" and "orbits Earth" in out.text and "steal" not in out.text
    assert out.report.verdict == "ok" and not out.asks_for_password


async def test_a_dangerous_link_is_not_fetched_at_all():
    calls = []

    async def fetch(url):
        calls.append(url)
        return fetched("x")

    out = await safe_open("https://paypa1-secure.com/verify", fetch)
    assert not out.opened and calls == [] and "did not open" in out.summary()


async def test_a_short_link_is_followed_and_the_destination_is_judged_again():
    async def fetch(url):
        return fetched("<title>Verify</title><input type='password'>", url="https://g00gle.com/login")

    out = await safe_open("https://bit.ly/3abcde", fetch)
    assert out.final_host == "g00gle.com" and out.report.verdict == "danger"
    assert "different site" in out.summary() and out.asks_for_password


async def test_a_page_that_asks_for_a_password_is_flagged():
    async def fetch(url):
        return fetched("<title>Sign in</title><form><input type=password name=p></form>", url=url)

    out = await safe_open("https://www.wikipedia.org/login", fetch)
    assert out.asks_for_password and "password" in out.summary().lower()


async def test_something_that_is_not_a_web_page_is_not_read():
    async def fetch(url):
        return fetched("MZ\x90", ctype="application/x-msdownload", url=url)

    out = await safe_open("https://www.wikipedia.org/a.exe", fetch)
    assert not out.opened and "not a web page" in out.summary()


async def test_fetch_clean_sends_no_cookies_and_keeps_none():
    client = httpx.AsyncClient()
    client.cookies.set("session", "abc", domain="shop.example")
    seen = {}

    async def fetch(method, url, **kw):
        seen["jar"] = list(client.cookies.jar)
        client.cookies.set("tracker", "xyz", domain="shop.example")
        return fetched("<p>hi</p>")

    await fetch_clean("https://shop.example/", fetch=fetch, client=client)
    assert seen["jar"] == [] and list(client.cookies.jar) == []
    await client.aclose()
