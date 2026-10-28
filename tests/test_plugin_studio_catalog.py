"""The studio tools around the catalogue: filing footage, today's style, preferences, discovery and the automatic background."""
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from minty_core.safefetch import Fetched
from minty_core.studio import discover
from tests.test_plugin_studio import build, ff, make_reel  # noqa: F401  (the same harness)

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


def clip(tmp_path, name, hue=0):
    p = tmp_path / f"{name}.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=5", "-vf", f"hue=h={hue}", "-pix_fmt", "yuv420p", str(p))
    return p


async def add(host, path, kind="gameplay", category="", **kw):
    args = {"action": "add", "path": str(path), "kind": kind, "license": "owned", "source": "my own recording", **kw}
    if category:
        args["category"] = category
    return await host.call("studio_make", args)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 2, 9, 0)

    def __call__(self):
        return self.now


def with_clock(tmp_path, **settings):
    clock = Clock()
    host, ig, _ = build(tmp_path, **settings)
    host._services["studio_now"] = clock
    return host, clock


async def test_the_tools_added_cost_what_they_should(tmp_path):
    host, _, _ = build(tmp_path)
    risk = host.get_tool("studio_make").risk
    assert risk({"action": "discover"}) == 1 and risk({"action": "catalog"}) == 0 and risk({"action": "prefer"}) == 0
    assert risk({"action": "shuffle"}) == 0 and risk({"action": "tag"}) == 0


async def test_own_footage_is_filed_under_a_style_and_the_catalogue_says_so(tmp_path):
    host, _ = with_clock(tmp_path)
    out = await add(host, clip(tmp_path, "mc1"), category="parkour")
    assert "Kept" in out
    assert "parkour: 1" in await host.call("studio_make", {"action": "catalog"})
    assert "category" in (await add(host, clip(tmp_path, "mc2", 40), category="cooking")).lower()  # an unknown style is refused, nothing kept


async def test_a_plain_gameplay_clip_defaults_to_the_gameplay_style(tmp_path):
    host, _ = with_clock(tmp_path)
    await add(host, clip(tmp_path, "g1"))
    assert "gameplay: 1" in await host.call("studio_make", {"action": "catalog"})


async def test_an_existing_clip_can_be_filed_later(tmp_path):
    host, _ = with_clock(tmp_path)
    out = await add(host, clip(tmp_path, "g1"))
    asset_id = out.split("asset ")[1].split(" ")[0]
    assert "parkour" in await host.call("studio_make", {"action": "tag", "asset": asset_id, "category": "parkour"})
    assert "parkour: 1" in await host.call("studio_make", {"action": "catalog"})


async def test_auto_picks_todays_style_and_says_which_and_whose(tmp_path):
    host, clock = with_clock(tmp_path)
    await add(host, clip(tmp_path, "mc1"), category="parkour")
    await add(host, clip(tmp_path, "g1", 80), category="gameplay")
    first = await host.call("studio_make", {"action": "video", "background": "auto", "seconds": 3, "name": "a1"})
    assert "Background:" in first and ("parkour" in first or "gameplay" in first) and "OK" in first or "Problems" in first
    style = "parkour" if "Background: parkour" in first else "gameplay"
    clock.now += timedelta(days=1)
    second = await host.call("studio_make", {"action": "video", "background": "auto", "seconds": 3, "name": "a2"})
    assert f"Background: {style}" not in second  # a different style the next day


async def test_a_named_style_can_be_asked_for(tmp_path):
    host, _ = with_clock(tmp_path)
    await add(host, clip(tmp_path, "mc1"), category="parkour")
    await add(host, clip(tmp_path, "g1", 80), category="gameplay")
    out = await host.call("studio_make", {"action": "video", "background": "gameplay,parkour", "seconds": 3, "name": "b1"})
    assert "Error" not in out  # kind words and styles still work in a list
    none = await host.call("studio_make", {"action": "video", "background": "nature", "seconds": 3, "name": "b2"})
    assert none.startswith("Error") and "nature" in none and "discover" in none


async def test_with_nothing_in_the_catalogue_a_plain_background_is_made_and_it_says_so(tmp_path):
    host, _ = with_clock(tmp_path)
    out = await host.call("studio_make", {"action": "video", "background": "auto", "seconds": 3, "name": "plain"})
    assert "plain generated background" in out and "discover" in out and "Error" not in out


async def test_preferences_turn_a_style_off_and_favour_a_clip_and_survive_a_reload(tmp_path):
    host, clock = with_clock(tmp_path)
    ids = []
    for name, cat, hue in (("a", "parkour", 0), ("b", "gameplay", 50)):
        out = await add(host, clip(tmp_path, name, hue), category=cat)
        ids.append(out.split("asset ")[1].split(" ")[0])
    assert "off" in await host.call("studio_make", {"action": "prefer", "category": "gameplay", "choice": "off"})
    for n in range(4):
        clock.now += timedelta(days=1)
        assert "Background: parkour" in await host.call("studio_make", {"action": "video", "background": "auto", "seconds": 3, "name": f"p{n}"})
    assert "favourite" in await host.call("studio_make", {"action": "prefer", "asset": ids[0], "choice": "favourite"})
    host2, _, _ = build(tmp_path)  # a restart: the same disk
    text = await host2.call("studio_make", {"action": "catalog"})
    assert "gameplay: 1 (off)" in text and "favourites: 1" in text
    assert "mode" in (await host2.call("studio_make", {"action": "prefer", "choice": "each_video"})).lower()
    assert "Error" in await host2.call("studio_make", {"action": "prefer", "category": "gameplay", "choice": "sideways"})


async def test_shuffle_changes_the_style_now(tmp_path):
    host, _ = with_clock(tmp_path)
    await add(host, clip(tmp_path, "a"), category="parkour")
    await add(host, clip(tmp_path, "b", 50), category="gameplay")
    first = await host.call("studio_make", {"action": "video", "background": "auto", "seconds": 3, "name": "s1"})
    shuffled = await host.call("studio_make", {"action": "shuffle"})
    now = await host.call("studio_make", {"action": "video", "background": "auto", "seconds": 3, "name": "s2"})
    style = lambda t: "parkour" if "Background: parkour" in t else "gameplay"  # noqa: E731
    assert style(first) != style(now) and style(now) in shuffled


def scripted_nasa(tmp_path, monkeypatch, n=2):
    vids = [clip(tmp_path, f"nasa{i}", i * 60).read_bytes() for i in range(n)]
    ids = [f"Space{i}" for i in range(n)]
    routes = {"https://images-api.nasa.gov/search": json.dumps({"collection": {"items": [
        {"data": [{"nasa_id": i, "title": f"Nebula {i}", "description": "Gas.", "media_type": "video"}],
         "href": f"https://images-assets.nasa.gov/video/{i}/collection.json"} for i in ids]}}).encode()}
    for k, i in enumerate(ids):
        routes[f"https://images-assets.nasa.gov/video/{i}/collection.json"] = json.dumps(
            [f"http://images-assets.nasa.gov/video/{i}/{i}~medium.mp4"]).encode()
        routes[f"https://images-assets.nasa.gov/video/{i}/{i}~medium.mp4"] = vids[k]
    asked = []

    async def net(method, url, **kw):
        asked.append(url)
        if url.startswith("https://api.pexels.com/"):  # the test key is set: that provider is asked too, and has nothing
            return Fetched(200, "application/json", b'{"videos": []}', "utf-8", url=url)
        data = next(v for k, v in routes.items() if url.startswith(k))
        return Fetched(200, "video/mp4" if url.endswith(".mp4") else "application/json", data, "utf-8", url=url)

    monkeypatch.setattr(discover, "safe_fetch", net)
    return asked


async def test_discover_fetches_new_footage_once_a_day_and_credits_it(tmp_path, monkeypatch):
    host, clock = with_clock(tmp_path)
    scripted_nasa(tmp_path, monkeypatch)
    out = await host.call("studio_make", {"action": "discover"})
    assert "space" in out and "nasa" in out.lower() and "2 new" in out
    assert "space: 2" in await host.call("studio_make", {"action": "catalog"})
    assert "nothing" in (await host.call("studio_make", {"action": "discover"})).lower()  # searched today already
    clock.now += timedelta(days=1)
    assert "0 new" in await host.call("studio_make", {"action": "discover"})  # tomorrow it looks again; it has these already


async def test_the_library_stays_inside_its_size_limit(tmp_path, monkeypatch):
    host, _ = with_clock(tmp_path, library_max_mb=0.000001)
    scripted_nasa(tmp_path, monkeypatch)
    out = await host.call("studio_make", {"action": "discover"})
    assert "removed" in out
    assert "space: 0" in await host.call("studio_make", {"action": "catalog"})


async def test_discovery_can_run_by_itself_in_the_quiet_window_only_when_switched_on(tmp_path, monkeypatch):
    host, _ = with_clock(tmp_path)
    assert "studio" not in host._services.get("maintenance_tasks", {})
    (tmp_path / "second").mkdir()
    host2, _ = with_clock(tmp_path / "second", auto_discover=True)
    scripted_nasa(tmp_path, monkeypatch)
    result = await host2._services["maintenance_tasks"]["studio"]()
    assert "space" in result and "2 new" in result


async def test_a_removed_clip_is_not_offered_again_and_the_library_says_so(tmp_path, monkeypatch):
    host, _ = with_clock(tmp_path, library_max_mb=0.000001)
    scripted_nasa(tmp_path, monkeypatch)
    await host.call("studio_make", {"action": "discover"})
    lib = await host.call("studio_make", {"action": "library"})
    assert "file removed" in lib
    first_id = lib.splitlines()[0].split(".")[0]
    out = await host.call("studio_make", {"action": "video", "background": first_id, "seconds": 3, "name": "gone"})
    assert out.startswith("Error") and "removed" in out
    assert "Error" not in await host.call("studio_make", {"action": "video", "background": "auto", "seconds": 3, "name": "plain2"})  # a plain one
