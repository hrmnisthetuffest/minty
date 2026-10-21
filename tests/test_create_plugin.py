"""The create tools as the model sees them, through the real plugin host (feeds, arXiv, Cloudflare and speech are fakes; ffmpeg is real)."""
import base64
import json
import os
import shutil
import subprocess
import zipfile

import pytest

from tests.plugin_helpers import make_host
from tests.test_create_feeds_papers import ARXIV, ATOM, RSS, OUTLINE
from tests.test_create_images_translate_evidence import ACCOUNT, PNG, WIKI, ARXIV as ARXIV_CLAIM
from tests.test_create_media_notes import LECTURE, sha, tone

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="no ffmpeg")


class Rig:
    def __init__(self, tmp_path, keys=True, **create):
        self.pages = {"https://sci.example/feed": RSS, "https://tech.example/feed": ATOM}
        self.posts, self.arxiv = [], ARXIV
        self.heard = []

        async def fetch(url):
            if url not in self.pages:
                raise RuntimeError("no such feed")
            return self.pages[url]

        async def get(url, params=None):
            if "arxiv" in url and params and "search_query" in params and "all:sparse" in params["search_query"]:
                return self.arxiv
            if "wikipedia" in url:
                return WIKI
            if "arxiv" in url:
                return ARXIV_CLAIM
            raise RuntimeError("provider down")

        async def post(url, headers, body):
            self.posts.append((url, headers, body))
            return 200, {"success": True, "result": {"image": base64.b64encode(PNG).decode()}}

        async def nap(_s):
            return None

        secrets = {"plugin.create.cf_token": "tok", "plugin.create.cf_account": ACCOUNT} if keys else {}
        self.host = make_host(tmp_path / "cfg", secrets=secrets, services={
            "create_fetch": fetch, "create_get": get, "create_post": post, "create_sleep": nap})
        data = self.host._store.config.model_dump(mode="json")
        data["create"].update({"enabled": True, "images": True, "folder": str(tmp_path / "made"),
                               "media_folders": [str(tmp_path / "media")], **create})
        self.host._store.update(data)
        self.tmp = tmp_path

    async def call(self, tool, args):
        return await self.host.call(f"create_{tool}", args)


async def test_everything_is_off_until_switched_on_and_images_have_their_own_switch(tmp_path):
    r = Rig(tmp_path, enabled=False)
    for tool, args in (("news", {}), ("papers", {}), ("inbox", {}), ("make", {"outline": "a\n  b"}), ("translate", {"lang": "hi"}),
                       ("evidence", {"claim": "x"}), ("media", {}), ("sources", {})):
        assert "create.enabled" in await r.call(tool, args), tool
    assert "create.enabled" in await r.call("image", {"prompt": "a fox"})
    assert "create.images" in await Rig(tmp_path / "b", images=False).call("image", {"prompt": "a fox"})
    assert not (tmp_path / "cfg" / "create.db").exists()


async def test_the_news_brief_cites_outlets_and_feeds_can_be_added_and_removed(tmp_path):
    r = Rig(tmp_path)
    assert "No news feeds" in await r.call("news", {"action": "brief"})
    for url in ("https://sci.example/feed", "https://tech.example/feed"):
        assert "Added the feed" in await r.call("news", {"action": "add_feed", "url": url})
    assert "ordinary web address" in await r.call("news", {"action": "add_feed", "url": "file:///etc/passwd"})
    out = await r.call("news", {"action": "brief"})
    assert out.startswith("[untrusted") and "Daily Science" in out and "https://tech.example/kernel" in out
    assert "Removed." == await r.call("news", {"action": "remove_feed", "url": "https://tech.example/feed"})
    assert "tech.example" not in await r.call("news", {"action": "feeds"})


async def test_the_paper_watch_baselines_then_reports_new_papers(tmp_path):
    r = Rig(tmp_path)
    assert "Watching" in await r.call("papers", {"action": "watch", "topic": "sparse attention"})
    assert "No new papers" in await r.call("papers", {"action": "check"})  # first look: baseline
    r.arxiv = ARXIV.replace("</feed>", """<entry><id>http://arxiv.org/abs/2610.00009v1</id><published>2026-10-18T00:00:00Z</published>
        <title>Fresh sparse result</title><summary>y</summary><author><name>D. Rao</name></author></entry></feed>""")
    r.host._services["create"].papers().min_gap = 21600
    p = r.host._services["create"]
    p._clock = lambda: __import__("time").time() + 86400  # a day later
    out = await r.call("papers", {"action": "check"})
    assert out.startswith("[untrusted") and "Fresh sparse result" in out
    assert "sparse attention" in await r.call("papers", {"action": "list"})


async def test_the_idea_inbox_and_its_weekly_review(tmp_path):
    r = Rig(tmp_path)
    assert "empty" in await r.call("inbox", {"action": "list"})
    assert "Noted (number 1)" in await r.call("inbox", {"action": "add", "text": "a school podcast"})
    assert "[1] a school podcast" in await r.call("inbox", {"action": "list"})
    assert "1 new this week" in await r.call("inbox", {"action": "review"})
    assert "needs the notes folder" in await r.call("inbox", {"action": "promote", "id": 1})
    assert "Ticked off." == await r.call("inbox", {"action": "done", "id": 1})


async def test_mind_maps_slides_and_documents_are_made_as_new_files(tmp_path):
    r = Rig(tmp_path)
    out = await r.call("make", {"kind": "mindmap", "outline": OUTLINE})
    assert "mind map" in out
    made = tmp_path / "made"
    assert (made / "solar-system-mind-map.svg").is_file() and (made / "solar-system-mind-map.mmd").is_file()
    deck = await r.call("make", {"kind": "slides", "outline": OUTLINE, "title": "The sky"})
    assert "slides" in deck and "could not open the .pptx" in deck
    assert zipfile.is_zipfile(made / "the-sky.pptx") and (made / "the-sky.md").is_file()
    again = await r.call("make", {"kind": "slides", "outline": OUTLINE, "title": "The sky"})
    assert (made / "the-sky-1.pptx").is_file() and (made / "the-sky.pptx").read_bytes() != b""
    assert "web page version" in await r.call("make", {"kind": "document", "outline": OUTLINE})
    assert "Give me an outline" in await r.call("make", {"kind": "mindmap", "outline": "  "})
    assert "Say mindmap" in await r.call("make", {"kind": "poster", "outline": OUTLINE})


async def test_the_image_tool_uses_the_users_keys_and_counts_the_allowance(tmp_path):
    r = Rig(tmp_path)
    out = await r.call("image", {"prompt": "a red fox reading"})
    assert "Made the picture" in out and "7 left today" in out
    assert r.posts[0][1]["Authorization"] == "Bearer tok"
    tool = r.host.get_tool("create_image")
    assert tool.risk == 1 and tool.egress({}) == "https://api.cloudflare.com/"
    assert "set-key plugin.create.cf_token" in await Rig(tmp_path / "nokeys", keys=False).call("image", {"prompt": "a fox"})
    assert "personal number" in await r.call("image", {"prompt": "my card is 4111 1111 1111 1111"})


async def test_translation_with_the_glossary(tmp_path):
    r = Rig(tmp_path)
    assert "Saved" in await r.call("translate", {"action": "add_term", "term": "Minty", "lang": "hindi", "translation": "मिंटी"})
    prep = await r.call("translate", {"action": "prepare", "text": "Minty is here.", "lang": "Hindi"})
    assert "मिंटी" in prep and "Translate the text into Hindi" in prep
    assert "Fix these" in await r.call("translate", {"action": "check", "text": "Minty is here.", "translated": "मिन्टी यहाँ है।",
                                                    "lang": "hi"})
    assert "uses every glossary term" in await r.call("translate", {"action": "check", "text": "Minty is here.",
                                                                     "translated": "मिंटी यहाँ है।", "lang": "hi"})
    assert "Minty = मिंटी" in await r.call("translate", {"action": "terms", "lang": "hi"})
    assert "Name a language" in await r.call("translate", {"action": "prepare", "text": "x", "lang": "not a language!!"})


async def test_claim_evidence_has_no_verdict_and_is_tagged_untrusted(tmp_path):
    r = Rig(tmp_path)
    out = await r.call("evidence", {"claim": "The Great Wall of China is 21,196 km long"})
    assert out.startswith("[untrusted") and "21,196" in out and "I do not decide who is right" in out


@needs_ffmpeg
async def test_media_tools_and_show_notes_from_audio_with_source_tracking(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    a, b = tone(media / "a.wav", 2), tone(media / "b.wav", 7, 660)
    r = Rig(tmp_path)
    before = sha(a)
    assert "2 seconds" in await r.call("media", {"action": "info", "path": str(a)})
    assert "Made the clip" in await r.call("media", {"action": "trim", "path": str(b), "start": "0:01", "end": "0:04"})
    assert "Joined them" in await r.call("media", {"action": "merge", "paths": [str(a), str(b)]})
    assert sha(a) == before and a.exists() and b.exists()
    assert "inside the media folders" in await r.call("media", {"action": "info", "path": "/etc/hostname"})

    async def transcribe(pcm, rate):
        r.heard.append(rate)
        return LECTURE[(len(r.heard) - 1) % 3][1]

    r.host._services["transcribe"] = transcribe
    out = await r.call("media", {"action": "notes", "path": str(b), "title": "Seven second talk"})
    assert out.startswith("[untrusted") and "Saved the show notes" in out and "Source: b.wav" in out
    assert "Every note's source is still there" in await r.call("sources", {"action": "audit"})
    os.unlink(b)
    audit = await r.call("sources", {"action": "audit"})
    assert "Seven second talk" in audit and "source removed: b.wav" in audit
    assert "Seven second talk" in await r.call("sources", {"action": "list"})


async def test_notes_from_pasted_text_and_the_missing_recogniser_are_handled(tmp_path):
    r = Rig(tmp_path)
    out = await r.call("media", {"action": "notes", "text": "First part of the talk about plants. " * 20, "title": "Plants"})
    assert "Saved the show notes" in out and "Source: pasted text" in out
    assert any(p.suffix == ".md" for p in (tmp_path / "made" / "notes").iterdir())
    assert "Give an audio file or the transcript" in await r.call("media", {"action": "notes", "title": "x"})
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    if shutil.which("ffmpeg"):
        f = tone(media / "t.wav", 2)
        assert "speech recogniser is not running" in await r.call("media", {"action": "notes", "path": str(f)})
