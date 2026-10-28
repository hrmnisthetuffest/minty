import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tests.plugin_helpers import make_host
from minty_core.studio import fetch
from minty_core.studio.instagram import InstagramError, Published

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # files may be brought in from the home folder only


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


class FakeKokoro:
    def get_voices(self):
        return ["am_michael", "bf_emma"]

    def create(self, text, voice, speed, lang):
        t = np.linspace(0, 3.0, 24000 * 3, endpoint=False)
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 24000


class FakeIG:
    """Stands in for the Graph API client: records what would be published."""

    def __init__(self, error=None):
        self.reels, self.carousels, self.error, self.numbers = [], [], error, {}

    async def publish_reel(self, video, caption, *, ai_generated=True, share_to_feed=True):
        self.reels.append((Path(video).name, caption, ai_generated))
        if self.error:
            raise self.error
        return Published("1790", "https://www.instagram.com/reel/XYZ/")

    async def refresh_token(self):
        if self.error:
            raise self.error
        return "REFRESHED-TOKEN", 5184000

    async def insights(self, media_id):
        return self.numbers.get(media_id, {"reach": 100, "likes": 7})

    async def publish_carousel(self, urls, caption, *, ai_generated=True):
        self.carousels.append((urls, caption))
        return Published("1791", "https://www.instagram.com/p/ABC/")


def build(tmp_path, ig=None, **settings):
    ig = ig or FakeIG()
    cfg = {"enabled": True, "ig_user_id": "1784", **settings}
    services = {"studio_voice_engine": FakeKokoro(), "studio_instagram": lambda user, token, host: ig}
    host = make_host(tmp_path, plugins={"studio": cfg}, secrets={"plugin.studio.ig_token": "TOKEN", "plugin.studio.pexels_key": "KEY"},
                     services=services)
    return host, ig, tmp_path


@pytest.fixture
def gameplay(tmp_path):
    p = tmp_path / "parkour.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=6", "-pix_fmt", "yuv420p", str(p))
    return p


async def make_reel(host, gameplay, name="r1", license="owned"):
    out = await host.call("studio_make", {"action": "add", "path": str(gameplay), "kind": "gameplay", "license": license,
                                          "source": "my own world"})
    assert "Kept" in out, out
    voice = await host.call("studio_make", {"action": "voice", "text": "Did you know a creeper cannot climb ladders?", "name": name})
    assert "seconds" in voice, voice
    return await host.call("studio_make", {"action": "video", "background": "gameplay", "voiceover": name, "music": "calm",
                                           "script": "Did you know a creeper cannot climb ladders?", "name": name})


def path_in(text: str) -> str:
    import re
    return re.search(r"/\S+?\.mp4", text).group(0)


def test_the_tools_and_what_they_cost_in_asking(tmp_path):
    host, _, _ = build(tmp_path)
    names = {s.name for s in host.tool_specs() if s.name.startswith("studio_")}
    assert names == {"studio_make", "studio_draft", "studio_send", "studio_posts"}
    risk = host.get_tool("studio_make").risk
    assert risk({"action": "voice"}) == 0 and risk({"action": "stock"}) == 1
    assert risk({"action": "add"}) == 2 and risk({"action": "url"}) == 2  # attesting a licence is a statement: asked
    assert not any(k in host.get_tool("studio_draft").parameters["properties"] for k in ("auto", "mode", "policy"))


async def test_a_voiceover_music_and_a_finished_vertical_video_with_captions(tmp_path, gameplay):
    host, _, _ = build(tmp_path)
    out = await make_reel(host, gameplay)
    assert "9:16" not in out and "OK:" in out and "1080x1920" in out and "captions" in out.lower()
    assert Path(path_in(out)).is_file()


async def test_the_library_lists_licences_and_files_outside_it_are_refused(tmp_path, gameplay):
    host, _, _ = build(tmp_path)
    await make_reel(host, gameplay)
    lib = await host.call("studio_make", {"action": "library"})
    assert "owned" in lib and "gameplay" in lib and "generated" in lib
    dot = tmp_path / ".ssh"
    dot.mkdir()
    (dot / "id").write_text("secret")
    assert "outside" in (await host.call("studio_make", {"action": "add", "path": str(dot / "id"), "kind": "image",
                                                         "license": "owned", "source": "x"})).lower()
    assert "licence" in await host.call("studio_make", {"action": "add", "path": str(gameplay), "kind": "gameplay", "license": "yt", "source": "x"})


async def test_stock_footage_is_fetched_with_its_credit_and_the_key_comes_from_the_keystore(tmp_path, monkeypatch):
    host, _, _ = build(tmp_path)
    ff("-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=2", "-pix_fmt", "yuv420p", str(tmp_path / "s.mp4"))
    real = (tmp_path / "s.mp4").read_bytes()
    pexels = {"videos": [{"url": "https://www.pexels.com/video/1/", "duration": 5, "user": {"name": "Ana"},
                          "video_files": [{"link": "https://videos.pexels.com/x.mp4", "width": 720, "height": 1280,
                                           "file_type": "video/mp4"}]}]}
    seen = []

    async def net(method, url, **kw):
        seen.append(kw.get("auth_headers"))
        data = json.dumps(pexels).encode() if "search" in url else real
        return SimpleNamespace(status=200, content_type="application/json" if "search" in url else "video/mp4", data=data,
                               truncated=False, url=url)

    monkeypatch.setattr(fetch, "safe_fetch", net)
    out = await host.call("studio_make", {"action": "stock", "query": "city night", "kind": "video"})
    assert "Kept" in out and "pexels" in out.lower() and seen[0] == {"Authorization": "KEY"}


async def test_a_draft_runs_the_checks_and_says_what_the_rules_do_with_it(tmp_path, gameplay):
    host, _, _ = build(tmp_path)
    video = path_in(await make_reel(host, gameplay))
    out = await host.call("studio_draft", {"kind": "reel", "files": [video], "caption": "Minecraft parkour in 20 seconds #minecraft"})
    assert "Draft 1" in out and "ask you first" in out and "ask first" in out
    dup = await host.call("studio_draft", {"kind": "reel", "files": [video], "caption": "same again #minecraft"})
    assert dup.startswith("Error") and "post 1" in dup
    assert (await host.call("studio_draft", {"kind": "reel", "files": ["/etc/passwd"], "caption": "x"})).startswith("Error")


async def test_a_broken_video_is_refused_and_not_even_queued(tmp_path):
    host, _, _ = build(tmp_path)
    black = tmp_path / "black.mp4"
    ff("-f", "lavfi", "-i", "color=c=black:size=720x1280:rate=30:duration=4", "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(black))
    await host.call("studio_make", {"action": "add", "path": str(black), "kind": "video", "license": "owned", "source": "me"})
    lib_path = next(w for w in (await host.call("studio_make", {"action": "library"})).split() if w.endswith("black.mp4"))
    out = await host.call("studio_draft", {"kind": "reel", "files": [lib_path], "caption": "x #y"})
    assert out.startswith("Error") and "black" in out
    assert "No posts" in await host.call("studio_posts", {"action": "list"})


async def test_credits_the_licences_ask_for_are_added_to_the_caption(tmp_path, gameplay):
    host, _, _ = build(tmp_path)
    await host.call("studio_make", {"action": "add", "path": str(gameplay), "kind": "gameplay", "license": "cc-by",
                                    "source": "a site", "credit": "Clip by Ana, CC BY 4.0"})
    await host.call("studio_make", {"action": "voice", "text": "Did you know a creeper cannot climb ladders?", "name": "v"})
    video = path_in(await host.call("studio_make", {"action": "video", "background": "gameplay", "voiceover": "v", "name": "v"}))
    out = await host.call("studio_draft", {"kind": "reel", "files": [video], "caption": "Parkour #minecraft"})
    assert "Clip by Ana, CC BY 4.0" in out


async def gated(host, ident):
    host.get_tool("studio_send").risk({"id": ident})
    return await host.call("studio_send", {"id": ident})


async def drafted(host, gameplay, **kw):
    video = path_in(await make_reel(host, gameplay))
    await host.call("studio_draft", {"kind": "reel", "files": [video], "caption": "Minecraft parkour in 20 seconds #minecraft", **kw})


async def test_by_default_a_post_is_a_question_and_goes_only_after_a_yes(tmp_path, gameplay):
    host, ig, _ = build(tmp_path)
    await drafted(host, gameplay)
    tool = host.get_tool("studio_send")
    assert tool.risk({"id": 1}) == 3 and "Minecraft parkour" in tool.describe({"id": 1}) and "ask first" in tool.describe({"id": 1})
    out = await gated(host, 1)
    assert "Posted" in out and "after your yes" in out and "instagram.com/reel/XYZ" in out
    assert ig.reels[0][0].endswith(".mp4") and ig.reels[0][2] is True  # labelled as AI-made by default
    assert "posted" in await host.call("studio_posts", {"action": "list"})
    assert "already posted" in await gated(host, 1) and len(ig.reels) == 1


async def test_automatic_posting_only_when_the_user_switched_it_on_and_it_is_all_clean(tmp_path, gameplay):
    host, ig, _ = build(tmp_path, post_mode="auto")
    await drafted(host, gameplay)
    tool = host.get_tool("studio_send")
    assert tool.risk({"id": 1}) == 1
    assert "automatically" in await gated(host, 1)
    video2 = path_in(await make_reel(host, gameplay, name="r2"))
    await host.call("studio_draft", {"kind": "reel", "files": [video2], "caption": "Another one #parkour"})
    assert host.get_tool("studio_send").risk({"id": 2}) == 3  # the daily cap: the second is asked


async def test_an_unlicensed_file_is_never_automatic_even_in_auto_mode(tmp_path, gameplay):
    host, _, _ = build(tmp_path, post_mode="auto")
    stray = tmp_path / "work_stray.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=4", "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(stray))
    root = next(Path(str(tmp_path)).rglob("studio"), None) or tmp_path
    dest = root / "work"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(stray, dest / "stray.mp4")
    out = await host.call("studio_draft", {"kind": "reel", "files": [str(dest / "stray.mp4")], "caption": "Fine #caption"})
    assert "licence" in out and host.get_tool("studio_send").risk({"id": 1}) == 3


async def test_a_post_has_to_pass_the_safety_check_first(tmp_path, gameplay):
    host, ig, _ = build(tmp_path)
    await drafted(host, gameplay)
    out = await host.call("studio_send", {"id": 1})  # as if other code called it without the gate
    assert "safety check" in out and ig.reels == []


async def test_a_failure_before_publishing_is_failed_and_a_failure_after_the_request_is_unknown(tmp_path, gameplay):
    host, ig, _ = build(tmp_path, ig=FakeIG(error=InstagramError("Instagram could not process it (status ERROR)", False)))
    await drafted(host, gameplay)
    out = await gated(host, 1)
    assert out.startswith("Error") and "was not posted" in out and "failed" in await host.call("studio_posts", {"action": "list"})
    ig.error = InstagramError("could not reach Instagram (ReadTimeout)", None)
    out = await gated(host, 1)
    assert "may or may not" in out and "unknown" in await host.call("studio_posts", {"action": "list"})
    assert "might already" in await gated(host, 1)


async def test_sending_is_blocked_until_the_account_is_set_up(tmp_path):
    host = make_host(tmp_path, plugins={"studio": {"enabled": True}}, services={})
    why = host.get_tool("studio_send").blocked_when({"id": 1})
    assert "studio.ig_user_id" in why and "plugin.studio.ig_token" in why


async def test_a_carousel_needs_a_public_folder_and_checks_the_pictures_are_reachable(tmp_path, monkeypatch):
    pics = []
    for i in range(2):
        p = tmp_path / f"slide{i}.png"
        ff("-f", "lavfi", "-i", f"color=c={['white', '0xdddddd'][i]}:size=1080x1350", "-frames:v", "1", str(p))
        pics.append(p)
    public = tmp_path / "public"
    host, ig, _ = build(tmp_path, public_dir=str(public), public_base_url="https://pics.example.com/minty")
    for p in pics:
        assert "Kept" in await host.call("studio_make", {"action": "add", "path": str(p), "kind": "image", "license": "owned", "source": "me"})
    lib = (await host.call("studio_make", {"action": "library", "kind": "image"})).split()
    files = [w for w in lib if w.endswith(".png")]
    out = await host.call("studio_draft", {"kind": "carousel", "files": files, "caption": "Slides #minecraft"})
    assert "Draft 1" in out

    async def unreachable(method, url, **kw):
        return SimpleNamespace(status=404, content_type="text/html", data=b"", truncated=False, url=url)

    monkeypatch.setattr(fetch, "safe_fetch", unreachable)
    out = await gated(host, 1)
    assert out.startswith("Error") and "reachable" in out and ig.carousels == []
    assert len(list(public.glob("*.jpg"))) == 2  # converted to JPEG, the only format Instagram takes

    async def reachable(method, url, **kw):
        return SimpleNamespace(status=200, content_type="image/jpeg", data=b"", truncated=False, url=url)

    monkeypatch.setattr(fetch, "safe_fetch", reachable)
    assert "Posted" in await gated(host, 1)
    assert ig.carousels[0][0][0].startswith("https://pics.example.com/minty/")


async def test_a_carousel_without_a_public_address_says_what_to_set_up(tmp_path):
    host, _, _ = build(tmp_path)
    pics = []
    for i in range(2):
        p = tmp_path / f"s{i}.png"
        ff("-f", "lavfi", "-i", f"color=c={['white', '0xdddddd'][i]}:size=1080x1350", "-frames:v", "1", str(p))
        await host.call("studio_make", {"action": "add", "path": str(p), "kind": "image", "license": "owned", "source": "me"})
    lib = (await host.call("studio_make", {"action": "library", "kind": "image"})).split()
    await host.call("studio_draft", {"kind": "carousel", "files": [w for w in lib if w.endswith(".png")], "caption": "S #a"})
    out = await gated(host, 1)
    assert out.startswith("Error") and "public_base_url" in out


async def test_the_studio_offers_the_user_things_to_look_at_and_earned_independence(tmp_path, gameplay):
    host, _, _ = build(tmp_path, ig_token_expires="2000-01-01")
    loops = {l.key: l for l in host._services["studio_attention"]()}
    assert "studio:token" in loops and "expired" in loops["studio:token"].text and loops["studio:token"].urgency == "now"
    assert host._services["studio_lessons"]() == []


async def test_a_token_can_be_refreshed_and_the_warning_follows_the_new_expiry(tmp_path):
    from minty_core.keystore import SecretsStore
    host, _, _ = build(tmp_path, ig_token_expires="2000-01-01")
    assert host.get_tool("studio_make").risk({"action": "refresh_token"}) == 2  # a credential changes: asked
    assert "studio:token" in {l.key for l in host._services["studio_attention"]()}
    out = await host.call("studio_make", {"action": "refresh_token"})
    assert "60 days" in out and "REFRESHED" not in out  # the token itself is never shown
    assert SecretsStore(tmp_path).get("plugin.studio.ig_token") == "REFRESHED-TOKEN"
    assert "studio:token" not in {l.key for l in host._services["studio_attention"]()}  # the recorded refresh beats the old date


async def test_a_refused_refresh_changes_nothing(tmp_path):
    from minty_core.keystore import SecretsStore
    host, _, _ = build(tmp_path, ig=FakeIG(error=InstagramError("Instagram would not refresh the token (400): too new")))
    assert (await host.call("studio_make", {"action": "refresh_token"})).startswith("Error")
    assert SecretsStore(tmp_path).get("plugin.studio.ig_token") == "TOKEN"


async def test_how_posts_did_is_collected_and_the_best_kind_is_offered_as_evidence(tmp_path, gameplay):
    host, ig, _ = build(tmp_path)
    store = host._state["studio"]["posts"]
    for i in range(6):
        tags = "parkour" if i < 3 else "facts"
        p = store.add(kind="reel", files=[f"/x/{i}.mp4"], caption=f"post {i}", content_hash=f"h{i}", problems=[], licensed=True, tags=tags)
        store.claim(p.id, "approved")
        store.finish(p.id, "posted", media_id=f"10{i}0", permalink="https://instagram.com/reel/x/")
        ig.numbers[f"10{i}0"] = {"reach": 900 if tags == "parkour" else 300, "likes": 40}
    store._db.execute("UPDATE posts SET updated=updated-172800")  # they went out two days ago
    store._db.commit()
    out = await host.call("studio_posts", {"action": "insights"})
    assert "reach 900" in out and "reach 300" in out and "parkour" in out
    assert store.get(1).insights["reach"] == 900
    found = {l.key: l for l in host._services["studio_lessons"]()}
    assert set(found) == {"studio:perf:parkour", "studio:autopost"}  # six posts approved with nothing wrong: independence is offered too
    lesson = found["studio:perf:parkour"]
    assert "3.0x" in lesson.finding and lesson.confidence == "confirmed"
    assert "Nothing" not in lesson.proposal and "more" in lesson.proposal.lower()


async def test_too_few_posts_say_nothing_about_what_works(tmp_path):
    host, ig, _ = build(tmp_path)
    store = host._state["studio"]["posts"]
    for i in range(2):
        p = store.add(kind="reel", files=[f"/x/{i}.mp4"], caption="c", content_hash=f"h{i}", problems=[], licensed=True, tags="parkour")
        store.claim(p.id, "approved")
        store.finish(p.id, "posted", media_id=f"10{i}0")
    store._db.execute("UPDATE posts SET updated=updated-172800")
    store._db.commit()
    await host.call("studio_posts", {"action": "insights"})
    assert host._services["studio_lessons"]() == []
