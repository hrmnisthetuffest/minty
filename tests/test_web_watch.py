"""Watchers: polite by construction. A fake clock and a fake fetch; no network."""
import pytest

from minty_core.web.db import WebDB
from minty_core.web.watch import MIN_HOURS, Watcher

H = 3600.0
URL = "https://shop.example/p/earbuds"


def page(price=None, stock="InStock", extra=""):
    offer = f'"offers":{{"price":"{price}","priceCurrency":"INR","availability":"{stock}"}}' if price is not None else ""
    return ('<script type="application/ld+json">{"@type":"Product","name":"Boat Earbuds",' + offer + "}</script>"
            f"<p>{extra}</p>")


class Rig:
    def __init__(self, tmp_path, min_hours=6.0, jitter=0.5):
        self.t = 1_000_000.0
        self.pages: dict[str, object] = {}
        self.calls: list[tuple[float, str]] = []
        self.db = WebDB(tmp_path / "web.db")
        self.min_hours, self.jitter = min_hours, jitter
        self.w = self.make()

    def make(self):
        return Watcher(self.db, self.fetch, clock=lambda: self.t, min_hours=self.min_hours, rng=lambda: self.jitter)

    async def fetch(self, url):
        self.calls.append((self.t, url))
        got = self.pages[url]
        if isinstance(got, Exception):
            raise got
        return got if isinstance(got, tuple) else (200, got)


async def test_the_first_check_sets_a_baseline_and_says_what_it_will_do(tmp_path):
    r = Rig(tmp_path)
    r.pages[URL] = page(1499)
    wid = r.w.add(URL, "price", "Boat earbuds")
    out = await r.w.check_now(wid)
    assert len(out) == 1 and "Boat earbuds" in out[0] and "₹1,499" in out[0] and "every 6 hours" in out[0]
    assert "forbid" in r.w.terms_note()  # the per-site opt-in reminder


async def test_it_never_polls_faster_than_its_minimum_interval(tmp_path):
    r = Rig(tmp_path)
    r.pages[URL] = page(1499)
    r.w.add(URL, "price", "x")
    for _ in range(2200):  # a minute loop for a day and a half
        await r.w.run_due()
        r.t += 60
    times = [t for t, _ in r.calls]
    assert len(times) >= 5
    assert all(b - a >= 6 * H for a, b in zip(times, times[1:]))


def test_the_interval_has_a_floor_even_if_asked_for_less(tmp_path):
    r = Rig(tmp_path, min_hours=0.01)
    wid = r.w.add(URL, "price", hours=0.001)
    assert r.w.get(wid)["hours"] == MIN_HOURS >= 1.0


async def test_failures_back_off_and_a_success_resets_them(tmp_path):
    r = Rig(tmp_path, jitter=0.0)
    r.pages[URL] = RuntimeError("boom")
    wid = r.w.add(URL, "price", "x")
    gaps = []
    for _ in range(4):
        before = r.t
        await r.w.check_now(wid, force=True)
        gaps.append(r.w.get(wid)["next_check"] - before)
        r.t = r.w.get(wid)["next_check"]
    assert gaps == sorted(gaps) and gaps[1] >= 2 * gaps[0] and max(gaps) <= 48 * H
    r.pages[URL] = page(100)
    await r.w.check_now(wid, force=True)
    assert r.w.get(wid)["fails"] == 0 and r.w.get(wid)["next_check"] - r.t == pytest.approx(6 * H)


async def test_one_request_per_host_per_pass(tmp_path):
    r = Rig(tmp_path)
    other = "https://shop.example/p/watch"
    r.pages[URL], r.pages[other] = page(100), page(200)
    r.w.add(URL, "price", "a")
    r.w.add(other, "price", "b")
    await r.w.run_due()
    assert len(r.calls) == 1
    r.t += 60
    await r.w.run_due()
    assert len(r.calls) == 2  # the other one a minute later, never both at once


async def test_a_price_drop_a_target_and_a_return_to_stock_each_speak_once(tmp_path):
    r = Rig(tmp_path)
    r.pages[URL] = page(1499)
    wid = r.w.add(URL, "price", "Boat earbuds", target=1200)
    await r.w.check_now(wid, force=True)
    r.pages[URL] = page(1399)
    drop = await r.w.check_now(wid, force=True)
    assert len(drop) == 1 and "dropped from ₹1,499 to ₹1,399" in drop[0]
    r.pages[URL] = page(1150)
    hit = await r.w.check_now(wid, force=True)
    assert any("target" in m and "₹1,150" in m for m in hit)
    assert await r.w.check_now(wid, force=True) == []  # nothing new: silent
    r.pages[URL] = page(1300)
    assert await r.w.check_now(wid, force=True) == []  # a rise is not news
    r.pages[URL] = page(1300, stock="OutOfStock")
    assert await r.w.check_now(wid, force=True) == []
    r.pages[URL] = page(1300, stock="InStock")
    assert "back in stock" in (await r.w.check_now(wid, force=True))[0]


async def test_a_page_watch_says_what_sentences_changed_and_ignores_scripts_and_clocks(tmp_path):
    r = Rig(tmp_path)
    u = "https://school.example/notices"
    r.pages[u] = "<p>Exams start on 2 November. Bring a pen.</p><script>var a=1</script> 10:01"
    wid = r.w.add(u, "page", "School notices")
    await r.w.check_now(wid, force=True)
    r.pages[u] = "<p>Exams start on 2 November. Bring a pen.</p><script>var a=2</script> 11:45"
    assert await r.w.check_now(wid, force=True) == []
    r.pages[u] = "<p>Exams start on 9 November. Bring a pen.</p>"
    out = await r.w.check_now(wid, force=True)
    assert "changed" in out[0] and "9 November" in out[0] and "2 November" in out[0]


async def test_state_survives_a_restart_without_a_second_baseline(tmp_path):
    r = Rig(tmp_path)
    r.pages[URL] = page(1499)
    wid = r.w.add(URL, "price", "x")
    await r.w.check_now(wid, force=True)
    w2 = r.make()
    r.pages[URL] = page(1499)
    assert await w2.check_now(wid, force=True) == []
    assert w2.get(wid)["last_price"] == 1499.0


async def test_the_wishlist_shows_price_target_and_gap(tmp_path):
    r = Rig(tmp_path)
    r.pages[URL] = page(1499)
    wid = r.w.add(URL, "price", "Boat earbuds", target=1200, wish=True)
    await r.w.check_now(wid, force=True)
    r.w.add("https://shop.example/p/other", "page", "not a wish")
    rows = r.w.wishlist()
    assert len(rows) == 1 and rows[0]["name"] == "Boat earbuds" and rows[0]["price"] == 1499.0
    assert rows[0]["target"] == 1200.0 and rows[0]["gap"] == 299.0


@pytest.mark.parametrize("bad", ["", "ftp://x.example/a", "file:///etc/passwd", "https://user:pw@x.example/",
                                 "https://localhost/", "https://127.0.0.1/", "javascript:alert(1)"])
def test_only_ordinary_public_web_addresses_can_be_watched(tmp_path, bad):
    with pytest.raises(ValueError):
        Rig(tmp_path).w.add(bad, "price")


async def test_a_manual_check_is_not_a_loophole_for_hammering_a_site(tmp_path):
    r = Rig(tmp_path)
    r.pages[URL] = page(100)
    wid = r.w.add(URL, "price", "x")
    await r.w.check_now(wid)
    r.t += 120
    again = await r.w.check_now(wid)
    assert len(r.calls) == 1 and "checked" in again[0]
