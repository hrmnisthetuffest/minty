"""WP-16 acceptance:
* a watcher never polls faster than its minimum interval (fake clock);
* a downloaded file is hashed, looked up, quarantined, and only released on approval;
* a link from mail is judged first and read without cookies; an address on your own network is never fetched."""
import hashlib

from minty_core.safefetch import Fetched
from minty_core.web.kit import WebProvider
from minty_core.config import Config

H = 3600.0


def provider(tmp_path, fetch=None, http_get=None, key="vt-key", **web):
    cfg = Config()
    cfg.web.watch = cfg.web.downloads = cfg.web.helpers = True
    cfg.web.release_folder = str(tmp_path / "Downloads")
    for k, v in web.items():
        setattr(cfg.web, k, v)
    clock = [2_000_000.0]
    p = WebProvider(lambda: cfg, tmp_path / "cfg", {}, clock=lambda: clock[0], fetch=fetch, http_get=http_get,
                    secret=lambda name: key)
    return p, clock


def product(price):
    body = ('<script type="application/ld+json">{"@type":"Product","name":"Pen","offers":{"price":"%s",'
            '"priceCurrency":"INR","availability":"InStock"}}</script>' % price)
    f = Fetched(200, "text/html", body.encode(), "utf-8")
    f.url = "https://shop.example/pen"
    return f


async def test_the_loop_never_polls_faster_than_the_minimum_interval(tmp_path):
    calls = []
    p, clock = provider(tmp_path, fetch=None)

    async def fetch(url):
        calls.append(clock[0])
        return product(100)

    p._fetch = fetch
    p.watcher().add("https://shop.example/pen", "price", "Pen")
    for _ in range(60 * 72):  # the minute loop for three days
        await p.tick()
        clock[0] += 60
    assert len(calls) >= 8
    assert min(b - a for a, b in zip(calls, calls[1:])) >= 6 * H
    p.watcher().add("https://shop.example/pen", "price", "Pen", hours=0.01)  # asking for less changes nothing
    assert p.watcher().list()[0]["hours"] >= 6.0


async def test_a_download_is_hashed_looked_up_quarantined_and_released_only_on_approval(tmp_path):
    sha = hashlib.sha256(b"notes").hexdigest()
    asked = []

    async def http_get(url, headers):
        asked.append(url)
        return 404, {}

    p, _clock = provider(tmp_path, http_get=http_get)
    src = tmp_path / "dl" / "notes.pdf"
    src.parent.mkdir()
    src.write_bytes(b"notes")
    row = p.ingest_download(src, "notes.pdf", "https://school.example/")
    assert row["sha256"] == sha and not src.exists()  # hashed, and out of the workspace's reach
    assert (await p.quarantine().lookup(row["id"]))["state"] == "unknown"
    assert asked == [f"https://www.virustotal.com/api/v3/files/{sha}"]  # the hash, never the file
    assert not (tmp_path / "Downloads").exists()  # nothing released by itself
    # the tool that releases is a class-2 action: the Guard asks before it runs
    from tests.plugin_helpers import make_host

    host = make_host(tmp_path / "h")
    assert host.get_tool("web_download").risk({"action": "release"}) == 2
    dest = p.quarantine().release(row["id"], p.release_folder())
    assert dest == tmp_path / "Downloads" / "notes.pdf" and dest.read_bytes() == b"notes"


async def test_a_mail_link_is_judged_before_any_request_and_your_own_network_is_never_fetched(tmp_path):
    calls = []

    async def fetch(url):
        calls.append(url)
        return product(1)

    p, _clock = provider(tmp_path, fetch=fetch)
    own = await p.open("http://192.168.0.1/admin")
    phish = await p.open("https://paypa1-secure.com/verify")
    assert not own.opened and not phish.opened and calls == []
    assert "own network" in own.summary() and "looks like paypal" in phish.summary()
