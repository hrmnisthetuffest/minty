"""Every file the studio uses has a licence on record; a post made from anything else is never automatic."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from minty_core.safefetch import FetchFailed
from minty_core.studio import fetch
from minty_core.studio.assets import AssetError, AssetLibrary
from minty_core.studio.media import MediaError

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


def make_clip(path: Path, seconds=2, size="320x568"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@pytest.fixture
def lib(tmp_path):
    return AssetLibrary(tmp_path / "studio")


@pytest.fixture
def clip(tmp_path):
    return make_clip(tmp_path / "clip.mp4")


def test_a_file_needs_a_licence_from_the_list_before_it_is_kept(lib, clip):
    for bad in ("", "free", "youtube", "fair-use", None):
        with pytest.raises(AssetError, match="licence"):
            lib.add(clip, "video", bad, source="somewhere")
    asset = lib.add(clip, "video", "owned", source="I recorded it")
    assert asset.license == "owned" and Path(asset.path).is_file() and Path(asset.path).parent.name == "video"
    assert lib.add(clip, "video", "owned", source="again").id == asset.id  # the same bytes are one asset


def test_licences_that_need_a_credit_must_carry_one(lib, clip):
    with pytest.raises(AssetError, match="credit"):
        lib.add(clip, "video", "cc-by", source="a site")
    with pytest.raises(AssetError, match="credit"):
        lib.add(clip, "video", "pexels", source="pexels.com")
    a = lib.add(clip, "video", "cc-by", source="a site", attribution="Clip by Ana, CC BY 4.0")
    assert lib.credits([Path(a.path)]) == ["Clip by Ana, CC BY 4.0"]


def test_a_post_is_clean_only_if_every_file_in_it_is_on_record(lib, clip, tmp_path):
    mine = lib.add(clip, "video", "owned", source="me")
    stranger = make_clip(tmp_path / "stranger.mp4", seconds=3)
    assert lib.problems_for_posting([Path(mine.path)]) == []
    problems = lib.problems_for_posting([Path(mine.path), stranger])
    assert len(problems) == 1 and "stranger.mp4" in problems[0] and "licence" in problems[0]


def test_a_made_video_is_clean_when_everything_it_was_made_from_is(lib, clip, tmp_path):
    bg = lib.add(clip, "video", "owned", source="me")
    out = make_clip(tmp_path / "render.mp4", seconds=4)
    assert lib.problems_for_posting([out])  # unknown: nobody said where it came from
    render = lib.register_render(out, parents=[Path(bg.path)])
    assert render.kind == "render" and lib.problems_for_posting([Path(render.path)]) == []
    other = make_clip(tmp_path / "other.mp4", seconds=5)
    render2 = lib.register_render(make_clip(tmp_path / "r2.mp4", seconds=6), parents=[other])  # made from an unlicensed file
    assert lib.problems_for_posting([Path(render2.path)])


def test_listing_and_picking(lib, clip):
    lib.add(clip, "gameplay", "owned", source="my world", note="parkour tree")
    assert [a.kind for a in lib.list()] == ["gameplay"] and lib.list("music") == []
    assert lib.pick("gameplay").note == "parkour tree" and lib.pick("music") is None
    with pytest.raises(AssetError, match="kind"):
        lib.add(clip, "poster", "owned", source="x")


# ---- fetching ---------------------------------------------------------------------------------------------------------------
PEXELS = {"videos": [{
    "id": 7, "url": "https://www.pexels.com/video/7/", "duration": 12, "user": {"name": "Ana Videographer"},
    "video_files": [
        {"link": "https://videos.pexels.com/a-sd.mp4", "width": 360, "height": 640, "file_type": "video/mp4", "quality": "sd"},
        {"link": "https://videos.pexels.com/a-hd.mp4", "width": 1080, "height": 1920, "file_type": "video/mp4", "quality": "hd"},
        {"link": "https://videos.pexels.com/a-4k.mp4", "width": 2160, "height": 3840, "file_type": "video/mp4", "quality": "uhd"},
        {"link": "https://videos.pexels.com/a.webm", "width": 1080, "height": 1920, "file_type": "video/webm", "quality": "hd"}]}]}


class FakeNet:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def __call__(self, method, url, **kw):
        self.calls.append((url, kw))
        got = self.answers(url)
        if isinstance(got, Exception):
            raise got
        return got


def fetched(data, ctype, truncated=False):
    return SimpleNamespace(status=200, content_type=ctype, data=data, truncated=truncated, url="")


async def test_stock_search_picks_the_portrait_hd_mp4_that_is_not_4k(monkeypatch):
    net = FakeNet(lambda url: fetched(json.dumps(PEXELS).encode(), "application/json"))
    monkeypatch.setattr(fetch, "safe_fetch", net)
    found = await fetch.pexels_search("minecraft parkour", "KEY", per_page=3)
    assert found[0].link.endswith("a-hd.mp4") and found[0].photographer == "Ana Videographer" and found[0].duration == 12
    url, kw = net.calls[0]
    assert "orientation=portrait" in url or kw.get("params", {}).get("orientation") == "portrait"
    assert kw["auth_headers"] == {"Authorization": "KEY"}


async def test_a_stock_clip_is_downloaded_checked_and_kept_with_its_credit(lib, tmp_path, monkeypatch):
    real = make_clip(tmp_path / "real.mp4").read_bytes()
    net = FakeNet(lambda url: fetched(json.dumps(PEXELS).encode(), "application/json") if "search" in url
                  else fetched(real, "video/mp4"))
    monkeypatch.setattr(fetch, "safe_fetch", net)
    asset = await fetch.fetch_stock(lib, "city night", "KEY")
    assert asset.license == "pexels" and asset.attribution == "Video by Ana Videographer on Pexels"
    assert "https://www.pexels.com/video/7/" in asset.source  # the link stays in the record, not in the caption
    assert Path(asset.path).is_file()


async def test_no_key_means_a_clear_instruction_not_a_failure(lib):
    with pytest.raises(AssetError, match="set-key plugin.studio.pexels_key"):
        await fetch.fetch_stock(lib, "city", "")


async def test_a_url_needs_a_licence_you_state_and_must_really_be_media(lib, tmp_path, monkeypatch):
    real = make_clip(tmp_path / "real.mp4").read_bytes()
    monkeypatch.setattr(fetch, "safe_fetch", FakeNet(lambda url: fetched(real, "video/mp4")))
    with pytest.raises(AssetError, match="licence"):
        await fetch.fetch_url(lib, "https://example.com/a.mp4", "video", "", source="a friend")
    with pytest.raises(AssetError, match="licence"):
        await fetch.fetch_url(lib, "https://example.com/a.mp4", "video", "pexels", source="x")  # that one is for the stock search
    a = await fetch.fetch_url(lib, "https://example.com/a.mp4", "video", "permission", source="Sam said yes on 2026-10-01")
    assert a.license == "permission" and "Sam said yes" in a.source


async def test_a_web_page_a_huge_file_or_junk_is_not_kept(lib, tmp_path, monkeypatch):
    cases = [(fetched(b"<html>nope</html>", "text/html"), "not a video"),
             (fetched(b"x" * 100, "video/mp4", truncated=True), "too large"),
             (fetched(b"this is not really a video", "video/mp4"), "not a media file")]
    for answer, why in cases:
        monkeypatch.setattr(fetch, "safe_fetch", FakeNet(lambda url, a=answer: a))
        with pytest.raises((AssetError, MediaError), match=why):
            await fetch.fetch_url(lib, "https://example.com/a.mp4", "video", "owned", source="me")
    assert lib.list() == [] and list((tmp_path / "studio").rglob("*.mp4")) == []  # nothing half-kept


async def test_a_blocked_address_or_a_failed_request_is_a_clear_error(lib, monkeypatch):
    monkeypatch.setattr(fetch, "safe_fetch", FakeNet(lambda url: ValueError("blocked address")))
    with pytest.raises(AssetError, match="address"):
        await fetch.fetch_url(lib, "http://192.168.1.1/a.mp4", "video", "owned", source="me")
    monkeypatch.setattr(fetch, "safe_fetch", FakeNet(lambda url: FetchFailed("API request failed: timeout")))
    with pytest.raises(AssetError, match="timeout"):
        await fetch.fetch_url(lib, "https://example.com/a.mp4", "video", "owned", source="me")
