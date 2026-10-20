"""The web tools as the model sees them, through the real plugin host, with every outside thing faked."""
from types import SimpleNamespace

import pytest

from minty_core.safefetch import Fetched
from minty_core.webpage import Node
from tests.plugin_helpers import make_host

SHOP = "https://shop.example/p/earbuds"
PRODUCT = ('<script type="application/ld+json">{"@type":"Product","name":"Boat Earbuds","offers":{"price":"1499",'
           '"priceCurrency":"INR","availability":"InStock"}}</script><title>Boat</title>')


def fetched(body, url="", ctype="text/html; charset=utf-8"):
    f = Fetched(200, ctype, body.encode(), "utf-8")
    f.url = url
    return f


class Rig:
    def __init__(self, tmp_path, **web):
        self.pages = {SHOP: PRODUCT}
        self.opened_tabs, self.dry = [], []
        self.tab_list = ["https://a.example/1", "https://b.example/2"]
        self.page_info = None
        self.shown = []
        self.vt = {}
        self.ran = []
        self.nodes_after = None

        async def fetch(url):
            if url not in self.pages:
                raise RuntimeError("no such page")
            return fetched(self.pages[url], url)

        async def http_get(url, headers):
            return self.vt.get(url.rsplit("/", 1)[-1], (404, {}))

        async def run(cmd, cwd):
            self.ran.append(cmd)
            (cwd / "vid.en.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nFractions have a numerator. "
                                            "The numerator is on top.\n")
            return 0, ""

        async def page():
            return self.page_info

        async def tabs():
            return list(self.tab_list)

        async def open_tab(url):
            self.opened_tabs.append(url)
            return ""

        self.ops = SimpleNamespace(page=page, tabs=tabs, open_tab=open_tab,
                                   risky=lambda n: n.label == "Buy now", set_dry_run=lambda on: self.dry.append(on),
                                   dry_log=lambda: ["clicking “Buy now”"])

        async def nap(_s):
            if self.nodes_after is not None and self.page_info is not None:
                self.page_info = {**self.page_info, "nodes": self.nodes_after}

        self.host = make_host(tmp_path / "cfg", services={
            "web_fetch": fetch, "web_http_get": http_get, "web_run": run, "browser_ops": self.ops,
            "web_opener": lambda path: self.shown.append(path) or True, "web_sleep": nap,
            "publish": lambda *a, **k: None})
        data = self.host._store.config.model_dump(mode="json")
        data["web"].update({"watch": True, "downloads": True, "helpers": True,
                            "release_folder": str(tmp_path / "Downloads"), **web})
        self.host._store.update(data)
        self.host._services["browser_ops"] = self.ops  # the real browser plugin registers its own at load
        self.tmp = tmp_path

    async def call(self, tool, args):
        return await self.host.call(f"web_{tool}", args)


async def test_everything_is_off_until_switched_on(tmp_path):
    r = Rig(tmp_path, watch=False, downloads=False, helpers=False)
    assert "web.watch" in await r.call("watch", {"action": "list"})
    assert "web.downloads" in await r.call("download", {"action": "list"})
    for tool, args in (("open", {"url": SHOP}), ("later", {"action": "list"}), ("session", {"action": "list"}),
                       ("page", {"action": "clickable"}), ("formlog", {"action": "sites"}),
                       ("captions", {"url": "https://youtu.be/abc"})):
        assert "web.helpers" in await r.call(tool, args), tool
    assert not (tmp_path / "cfg" / "web.db").exists()


async def test_a_watch_is_added_with_a_baseline_and_the_terms_note_once(tmp_path):
    r = Rig(tmp_path)
    out = await r.call("watch", {"action": "add", "url": SHOP, "label": "Boat earbuds", "target": 1200, "wish": True})
    assert "Added watch 1." in out and "₹1,499" in out and "forbid" in out
    second = await r.call("watch", {"action": "add", "url": SHOP + "?v=2", "kind": "page", "label": "Same shop"})
    assert "forbid" not in second
    listing = await r.call("watch", {"action": "list"})
    assert listing.startswith("[personal data") and "Boat earbuds (price, every 6 h): ₹1,499, in stock, target ₹1,200" in listing
    assert "₹299 to go" in await r.call("watch", {"action": "wishlist"})
    assert "Removed it." == await r.call("watch", {"action": "remove", "id": 2})
    assert "so soon" in await r.call("watch", {"action": "check", "id": 1})  # not a way to hammer a site


async def test_a_watch_on_a_private_address_is_refused(tmp_path):
    r = Rig(tmp_path)
    assert "own network" in await r.call("watch", {"action": "add", "url": "http://192.168.1.5/admin"})


async def test_open_judges_the_link_and_marks_the_page_untrusted(tmp_path):
    r = Rig(tmp_path)
    r.pages["https://www.wikipedia.org/wiki/Moon"] = "<title>Moon</title><p>The Moon orbits Earth.</p>"
    good = await r.call("open", {"url": "https://www.wikipedia.org/wiki/Moon"})
    assert good.startswith("[untrusted web page content") and "orbits Earth" in good
    bad = await r.call("open", {"url": "https://paypa1-secure.com/verify"})
    assert "did not open it" in bad
    assert r.host.get_tool("web_open").risk == 0


async def test_read_later_round_trip(tmp_path):
    r = Rig(tmp_path)
    r.pages["https://news.example/a"] = ("<title>Moon news</title><p>The Moon is Earth's satellite. It formed long ago. "
                                         "Astronauts landed on the Moon in 1969. The Moon has no air.</p>")
    assert "Saved for later (number 1)" in await r.call("later", {"action": "add", "url": "https://news.example/a"})
    assert "[1] https://news.example/a" in await r.call("later", {"action": "list"})
    read = await r.call("later", {"action": "read"})
    assert read.startswith("[untrusted web page content") and "Moon" in read
    assert "Marked as read." == await r.call("later", {"action": "done", "id": 1})
    assert "empty" in await r.call("later", {"action": "list"})


async def test_show_makes_a_page_and_opens_it_only_when_asked(tmp_path):
    r = Rig(tmp_path)
    items = [{"title": "Boat", "text": "₹1,499", "url": SHOP}]
    out = await r.call("show", {"title": "Earbuds", "items": items})
    assert "Made a page:" in out and r.shown == []
    out = await r.call("show", {"title": "Earbuds", "items": items, "open": True})
    assert "Opened it." in out and len(r.shown) == 1 and (tmp_path / "cfg" / "show").is_dir()
    assert r.host.get_tool("web_show").risk({"open": True}) == 1 and r.host.get_tool("web_show").risk({}) == 0


async def test_sessions_are_saved_from_the_open_tabs_and_restored_into_new_ones(tmp_path):
    r = Rig(tmp_path)
    assert "Saved 2 tabs as homework" in await r.call("session", {"action": "save", "name": "Homework"})
    assert "homework: 2 tabs" in await r.call("session", {"action": "list"})
    assert "Opened 2 tabs from homework" in await r.call("session", {"action": "restore", "name": "homework"})
    assert r.opened_tabs == ["https://a.example/1", "https://b.example/2"]
    assert "no such session" in await r.call("session", {"action": "restore", "name": "nope"})


async def test_downloads_wait_in_quarantine_until_an_approved_release(tmp_path):
    r = Rig(tmp_path)
    src = tmp_path / "incoming.pdf"
    src.write_bytes(b"hello")
    row = r.host._services["web_download"](src, "notes.pdf", "https://school.example/")
    assert row["status"] == "quarantined" and not src.exists()
    assert "notes.pdf" in await r.call("download", {"action": "list"})
    assert "set-key plugin.web.virustotal_key" in await r.call("download", {"action": "check", "id": row["id"]})
    assert r.host.get_tool("web_download").risk({"action": "release"}) == 2
    assert r.host.get_tool("web_download").risk({"action": "list"}) == 0
    out = await r.call("download", {"action": "release", "id": row["id"]})
    assert "Released notes.pdf" in out and (tmp_path / "Downloads" / "notes.pdf").read_bytes() == b"hello"


async def test_with_the_download_switch_off_the_browser_hook_leaves_the_file_alone(tmp_path):
    r = Rig(tmp_path, downloads=False)
    src = tmp_path / "x.pdf"
    src.write_bytes(b"x")
    assert r.host._services["web_download"](src, "x.pdf", "") is None and src.exists()


PAGE = [Node("button", "Add to cart", "e1"), Node("button", "Buy now", "e2"), Node("link", "Home", "e3", href="/")]


async def test_the_page_tool_describes_clickables_and_toggles_dry_run(tmp_path):
    r = Rig(tmp_path)
    assert "No page is open" in await r.call("page", {"action": "clickable"})
    r.page_info = {"title": "Boat", "url": SHOP, "nodes": PAGE}
    out = await r.call("page", {"action": "clickable"})
    assert "2 buttons: Add to cart, Buy now" in out and "Careful with: Buy now." in out
    assert "Dry run is on" in await r.call("page", {"action": "dryrun_on"}) and r.dry == [True]
    assert "clicking “Buy now”" in await r.call("page", {"action": "dryrun_log"})
    assert "Dry run is off" in await r.call("page", {"action": "dryrun_off"}) and r.dry == [True, False]


async def test_login_hand_over_waits_for_the_user_and_never_types(tmp_path):
    r = Rig(tmp_path)
    login = [Node("textbox", "Email", "e1"), Node("textbox", "Password", "e2")]
    r.page_info = {"title": "Sign in", "url": "https://bank.example/login", "nodes": login}
    started = await r.call("page", {"action": "handover_start"})
    assert "I will not type your password" in started
    assert "Still waiting" in await r.call("page", {"action": "handover_check"})
    r.nodes_after = [Node("heading", "Welcome back", "")]  # the user logs in while we wait
    assert "carry on" in await r.call("page", {"action": "handover_check", "wait_s": 30})


async def test_captions_are_summarised_and_say_what_they_are(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/yt-dlp")
    r = Rig(tmp_path)
    out = await r.call("captions", {"url": "https://youtu.be/abc123"})
    assert out.startswith("[untrusted") and "numerator" in out and r.ran[0][0] == "/usr/bin/yt-dlp"
    assert "Give a YouTube video address" in await r.call("captions", {"url": "https://evil.example/x"})


async def test_the_form_log_hook_and_tool(tmp_path):
    r = Rig(tmp_path)
    r.host._services["web_formlog"]("shop.example", "email")
    assert "shop.example: email" in await r.call("formlog", {"action": "sites"})
    assert "email: 1x" in await r.call("formlog", {"action": "seen", "host": "shop.example"})
    assert "Forgot 1 record" in await r.call("formlog", {"action": "forget", "host": "shop.example"})
    assert "not filled" in await r.call("formlog", {"action": "sites"})
