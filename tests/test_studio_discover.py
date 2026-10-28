"""Finding new background footage from providers whose terms allow it: NASA's library (no key), Pexels and Pixabay (your own free keys).

Everything is untrusted: addresses must be on the provider's own hosts, the bytes must be real media, a hit that looks like someone
else's work is skipped, and the search runs at most once a day for each provider and style. Here the network is a script.
"""
import json
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest

from minty_core.safefetch import FetchFailed, Fetched
from minty_core.studio import catalog as cat
from minty_core.studio import discover
from minty_core.studio.assets import AssetLibrary

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")
NOW = datetime(2026, 11, 2, 9, 0)


def make_mp4(path, hue=0, seconds=4):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=24:duration={seconds}",
                    "-vf", f"hue=h={hue}", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)], check=True)
    return path.read_bytes()


@pytest.fixture(scope="module")
def mp4_bytes(tmp_path_factory):
    return make_mp4(tmp_path_factory.mktemp("clip") / "c.mp4")


@pytest.fixture(scope="module")
def many(tmp_path_factory):
    """Five different videos: a clip that decodes to the same picture is the same clip, however it is wrapped."""
    d = tmp_path_factory.mktemp("many")
    return [make_mp4(d / f"{i}.mp4", hue=i * 60) for i in range(5)]


@pytest.fixture
def world(tmp_path):
    lib = AssetLibrary(tmp_path / "lib")
    return lib, cat.Catalog(lib, min_clips=1)


class Net:
    """A scripted internet: url -> (status, content type, bytes), or an exception. Records what was asked."""

    def __init__(self, routes):
        self.routes, self.asked = routes, []

    async def __call__(self, method, url, **kw):
        self.asked.append((url, kw))
        route = next((v for k, v in self.routes.items() if url.startswith(k)), None)
        if route is None:
            raise AssertionError(f"unexpected request {url}")
        if isinstance(route, Exception):
            raise route
        status, ctype, data = route
        cap = kw.get("max_bytes", 1_000_000)
        return Fetched(status, ctype, data[:cap], "utf-8", truncated=len(data) >= cap, url=url)


def nasa_search(*items):
    return json.dumps({"collection": {"items": [
        {"data": [{"nasa_id": i["id"], "title": i.get("title", "Aurora"), "description": i.get("description", "Green light."),
                   "media_type": "video", "center": "GSFC", **({"secondary_creator": i["creator"]} if "creator" in i else {})}],
         "href": f"https://images-assets.nasa.gov/video/{i['id']}/collection.json"} for i in items]}}).encode()


def manifest(nid, variants=("medium", "small", "mobile", "orig")):
    return json.dumps([f"http://images-assets.nasa.gov/video/{nid}/{nid}~{v}.mp4" for v in variants] +
                      [f"http://images-assets.nasa.gov/video/{nid}/{nid}.vtt"]).encode()


def nasa_routes(video, *ids, extra=None):
    videos = video if isinstance(video, list) else [video]
    routes = {"https://images-api.nasa.gov/search": (200, "application/json", nasa_search(*[{"id": i} for i in ids]))}
    for n, i in enumerate(ids):
        routes[f"https://images-assets.nasa.gov/video/{i}/collection.json"] = (200, "application/json", manifest(i))
        routes[f"https://images-assets.nasa.gov/video/{i}/{i}~medium.mp4"] = (200, "video/mp4", videos[n % len(videos)])
    routes.update(extra or {})
    return routes


async def go(world, net, monkeypatch, provider="nasa", category="space", **kw):
    lib, catalog = world
    monkeypatch.setattr(discover, "safe_fetch", net)
    return await discover.discover(lib, catalog, provider, category, now=NOW, **kw)


async def test_nasa_clips_are_found_checked_kept_and_filed(world, monkeypatch, many):
    lib, catalog = world
    net = Net(nasa_routes(many, "AuroraA", "AuroraB"))
    report = await go(world, net, monkeypatch)
    assert (report.found, report.kept, report.error) == (2, 2, "")
    clips = catalog.clips("space")
    assert len(clips) == 2 and {c.provider for c in clips} == {"nasa"} and {c.external_id for c in clips} == {"AuroraA", "AuroraB"}
    a = clips[0].asset
    assert a.license == "nasa" and a.attribution == "Video: NASA" and "images.nasa.gov/details/" in a.source
    assert all(url.startswith("https://") for url, _ in net.asked)  # the manifest lists http addresses; they are fetched over https


async def test_a_hit_that_looks_like_someone_elses_work_is_skipped(world, monkeypatch, mp4_bytes):
    routes = nasa_routes(mp4_bytes, "Good", "Bad")
    routes["https://images-api.nasa.gov/search"] = (200, "application/json", nasa_search(
        {"id": "Good"}, {"id": "Bad", "description": "Footage © Getty Images, used with permission"}))
    report = await go(world, Net(routes), monkeypatch)
    assert report.kept == 1 and any("Bad" in s and "third party" in s for s in report.skipped)


async def test_what_was_fetched_before_is_not_fetched_again(world, monkeypatch, mp4_bytes):
    net = Net(nasa_routes(mp4_bytes, "AuroraA"))
    await go(world, net, monkeypatch)
    net2 = Net(nasa_routes(mp4_bytes, "AuroraA"))
    report = await go(world, net2, monkeypatch, force=True)
    assert report.kept == 0 and not any("~medium.mp4" in u for u, _ in net2.asked)


async def test_a_playlist_in_place_of_a_video_is_refused(world, monkeypatch):
    hls = b"#EXTM3U\n#EXTINF:5,\nfile:///etc/hostname\n"
    report = await go(world, Net(nasa_routes(hls, "Evil")), monkeypatch)
    assert report.kept == 0 and report.skipped and world[1].clips("space") == []


async def test_an_address_outside_the_providers_own_hosts_is_never_fetched(world, monkeypatch, mp4_bytes):
    routes = nasa_routes(mp4_bytes, "Odd")
    routes["https://images-assets.nasa.gov/video/Odd/collection.json"] = (200, "application/json", json.dumps(
        ["http://evil.example/Odd~medium.mp4", "http://127.0.0.1:8080/Odd~small.mp4"]).encode())
    net = Net(routes)
    report = await go(world, net, monkeypatch)
    assert report.kept == 0 and not any("evil.example" in u or "127.0.0.1" in u for u, _ in net.asked)


async def test_a_file_too_big_is_replaced_by_the_next_smaller_one(world, monkeypatch, mp4_bytes):
    routes = nasa_routes(mp4_bytes, "Big")
    routes["https://images-assets.nasa.gov/video/Big/Big~medium.mp4"] = (200, "video/mp4", mp4_bytes * 50)
    routes["https://images-assets.nasa.gov/video/Big/Big~small.mp4"] = (200, "video/mp4", mp4_bytes)
    report = await go(world, Net(routes), monkeypatch, max_bytes=len(mp4_bytes) * 5)
    assert report.kept == 1


async def test_only_so_many_are_kept_in_one_run(world, monkeypatch, many):
    ids = [f"Clip{i}" for i in range(5)]
    report = await go(world, Net(nasa_routes(many, *ids)), monkeypatch, limit=2)
    assert report.kept == 2


async def test_blocked_words_in_a_title_skip_the_hit(world, monkeypatch, mp4_bytes):
    routes = nasa_routes(mp4_bytes, "Gun1")
    routes["https://images-api.nasa.gov/search"] = (200, "application/json", nasa_search({"id": "Gun1", "title": "Weapon test footage"}))
    report = await go(world, Net(routes), monkeypatch, blocked=("weapon",))
    assert report.kept == 0 and any("blocked" in s for s in report.skipped)


async def test_a_provider_that_times_out_is_reported_and_asked_again_later_not_now(world, monkeypatch):
    lib, catalog = world
    net = Net({"https://images-api.nasa.gov/search": FetchFailed("API request failed: timeout")})
    report = await go(world, net, monkeypatch)
    assert report.kept == 0 and "timeout" in report.error
    ok, why = catalog.due("nasa", "space", NOW + timedelta(hours=1))
    assert not ok and "failed" in why
    assert catalog.due("nasa", "space", NOW + timedelta(hours=7))[0]


async def test_a_provider_is_searched_once_a_day_for_each_style(world, monkeypatch, mp4_bytes):
    lib, catalog = world
    await go(world, Net(nasa_routes(mp4_bytes, "A")), monkeypatch)
    assert not catalog.due("nasa", "space", NOW + timedelta(hours=5))[0]
    assert catalog.due("nasa", "space", NOW + timedelta(days=1))[0]
    assert catalog.due("nasa", "nature", NOW)[0]


async def test_a_rate_limited_provider_is_left_alone(world, monkeypatch):
    net = Net({"https://images-api.nasa.gov/search": (429, "application/json", b"{}")})
    report = await go(world, net, monkeypatch)
    assert report.kept == 0 and "429" in report.error or "limit" in report.error.lower()


def pexels_json(link, vid=111):
    return json.dumps({"videos": [{"id": vid, "url": f"https://www.pexels.com/video/{vid}/", "duration": 12, "user": {"name": "Ada Lens"},
                                   "video_files": [{"file_type": "video/mp4", "width": 1080, "height": 1920, "link": link}]}]}).encode()


async def test_pexels_needs_the_users_key_and_credits_the_creator(world, monkeypatch, mp4_bytes):
    lib, catalog = world
    link = "https://videos.pexels.com/video-files/111/111-hd_1080_1920_30fps.mp4"
    routes = {"https://api.pexels.com/v1/videos/search": (200, "application/json", pexels_json(link)), link: (200, "video/mp4", mp4_bytes)}
    none = await go(world, Net(routes), monkeypatch, provider="pexels", category="nature", key="")
    assert none.kept == 0 and "key" in none.error
    got = await go(world, Net(routes), monkeypatch, provider="pexels", category="nature", key="PEXELSKEY")
    assert got.kept == 1
    a = catalog.clips("nature")[0].asset
    assert a.license == "pexels" and a.attribution == "Video by Ada Lens on Pexels" and "http" not in a.attribution


async def test_pexels_downloads_only_from_pexels(world, monkeypatch, mp4_bytes):
    link = "https://cdn.evil.example/111.mp4"
    net = Net({"https://api.pexels.com/v1/videos/search": (200, "application/json", pexels_json(link))})
    report = await go(world, net, monkeypatch, provider="pexels", category="nature", key="K")
    assert report.kept == 0 and not any("evil" in u for u, _ in net.asked)


def pixabay_json(url, vid=222):
    return json.dumps({"hits": [{"id": vid, "pageURL": f"https://pixabay.com/videos/id-{vid}/", "duration": 9, "user": "Bob",
                                 "tags": "city, night", "videos": {"medium": {"url": url, "width": 1280, "height": 720, "size": 1000},
                                                                   "small": {"url": url, "width": 960, "height": 540, "size": 500}}}]}).encode()


async def test_pixabay_key_travels_in_the_request_parameters_only(world, monkeypatch, mp4_bytes):
    lib, catalog = world
    link = "https://cdn.pixabay.com/video/2024/01/01/222_medium.mp4"
    net = Net({"https://pixabay.com/api/videos/": (200, "application/json", pixabay_json(link)), link: (200, "video/mp4", mp4_bytes)})
    report = await go(world, net, monkeypatch, provider="pixabay", category="city", key="PIXKEY")
    assert report.kept == 1 and catalog.clips("city")[0].asset.license == "pixabay"
    search_url, kw = net.asked[0]
    assert "PIXKEY" not in search_url and kw.get("auth_params") == {"key": "PIXKEY"}


async def test_footage_that_only_you_can_supply_is_never_searched_for(world, monkeypatch):
    report = await go(world, Net({}), monkeypatch, provider="nasa", category="parkour")
    assert report.kept == 0 and "your own" in report.error


async def test_run_all_visits_each_available_provider_and_style_and_survives_a_failure(world, monkeypatch, mp4_bytes):
    lib, catalog = world
    routes = nasa_routes(mp4_bytes, "OnlyOne")
    net = Net(routes)
    monkeypatch.setattr(discover, "safe_fetch", net)
    reports = await discover.run_all(lib, catalog, keys={}, now=NOW)
    assert [(r.provider, r.category) for r in reports] == [("nasa", "space")]  # no keys: only the keyless provider
    assert reports[0].kept == 1
    again = await discover.run_all(lib, catalog, keys={}, now=NOW + timedelta(hours=2))
    assert again == []  # nothing is due yet


async def test_a_kept_clip_is_a_small_portrait_background_not_the_raw_download(world, monkeypatch, mp4_bytes):
    from pathlib import Path

    from minty_core.studio import media

    lib, catalog = world
    await go(world, Net(nasa_routes(mp4_bytes, "Wide")), monkeypatch)
    asset = catalog.clips("space")[0].asset
    info = await media.probe(Path(asset.path))
    assert (info.width, info.height) == (720, 1280) and info.vcodec == "h264" and not info.has_audio
    assert Path(asset.path).stat().st_size < len(mp4_bytes) * 6  # no bigger than it needs to be


async def test_a_long_download_is_cut_to_the_length_a_background_needs(world, monkeypatch, tmp_path):
    from pathlib import Path

    from minty_core.studio import media

    long = tmp_path / "long.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=12", "-pix_fmt", "yuv420p",
                    str(long)], check=True)
    monkeypatch.setattr(discover, "BG_SECONDS", 5.0)
    lib, catalog = world
    await go(world, Net(nasa_routes(long.read_bytes(), "Long")), monkeypatch)
    info = await media.probe(Path(catalog.clips("space")[0].asset.path))
    assert 4.5 <= info.duration <= 5.6


def test_the_hosts_a_provider_may_point_us_at_are_exact():
    ok = discover.host_ok
    assert ok("pexels", "https://videos.pexels.com/video-files/1/1.mp4")
    assert ok("pexels", "https://player.vimeo.com/external/291648067.hd.mp4?s=abc&profile_id=175")  # what Pexels' own documentation shows
    assert not ok("pexels", "https://player.vimeo.com/video/291648067")  # only the files Pexels hands out, not any Vimeo page
    assert not ok("pexels", "http://videos.pexels.com/x.mp4") and not ok("pexels", "https://videos.pexels.com.evil.example/x.mp4")
    assert not ok("pexels", "https://user:pw@videos.pexels.com/x.mp4") and not ok("pexels", "https://videos.pexels.com:8443/x.mp4")
    assert not ok("nasa", "https://videos.pexels.com/x.mp4") and ok("nasa", "https://images-assets.nasa.gov/video/a/a~mobile.mp4")
