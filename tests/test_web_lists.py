"""Read-later, captions, sessions and the form log."""
import pytest

from minty_core.web.captions import CaptionsUnavailable, fetch_captions, parse_vtt, valid_video_url
from minty_core.web.db import WebDB
from minty_core.web.formlog import FormLog
from minty_core.web.readlater import ReadLater
from minty_core.web.safeopen import Opened, UrlReport
from minty_core.web.sessions import Sessions


@pytest.fixture
def db(tmp_path):
    return WebDB(tmp_path / "web.db")


# ---- read-later ------------------------------------------------------------------------------------------
async def test_read_later_queues_fetches_summarises_and_marks_read(db):
    rl = ReadLater(db, clock=lambda: 1000.0)
    a = rl.add("https://news.example/moon")
    assert rl.add("https://news.example/moon") == a  # same page twice is one entry
    assert [r["url"] for r in rl.list()] == ["https://news.example/moon"]

    async def opener(url):
        text = ("The Moon is Earth's only natural satellite. It formed about 4.5 billion years ago. "
                "The Moon controls the tides. Astronauts first landed on the Moon in 1969. "
                "The Moon has no atmosphere to speak of. Its gravity is one sixth of Earth's.")
        return Opened(True, UrlReport(), title="All about the Moon", text=text, final_host="news.example")

    got = await rl.fetch(a, opener)
    assert got["title"] == "All about the Moon" and got["summary"] and "Moon" in got["summary"]
    assert rl.next()["id"] == a
    assert rl.mark_read(a) and rl.list() == [] and len(rl.list("read")) == 1
    assert rl.remove(a) and rl.list("read") == []


def test_only_ordinary_web_addresses_are_queued(db):
    rl = ReadLater(db)
    for bad in ("", "file:///etc/passwd", "javascript:alert(1)", "https://user:pw@x.example/"):
        with pytest.raises(ValueError):
            rl.add(bad)


async def test_a_page_that_cannot_be_read_keeps_its_place_with_a_note(db):
    rl = ReadLater(db)
    a = rl.add("https://down.example/")

    async def opener(url):
        return Opened(False, UrlReport(), note="I could not reach it.")

    got = await rl.fetch(a, opener)
    assert got["summary"].startswith("Could not read it") and rl.list()[0]["id"] == a


# ---- captions --------------------------------------------------------------------------------------------
VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000 align:start position:0%
Today we learn<00:00:00.500><c> about</c><00:00:01.000><c> fractions</c>

00:00:02.000 --> 00:00:04.000 align:start position:0%
Today we learn about fractions
A fraction has a top and a bottom

00:00:04.000 --> 00:00:06.000
A fraction has a top and a bottom
&nbsp;
The top is the numerator
"""


def test_vtt_becomes_plain_text_without_the_rolling_repeats():
    assert parse_vtt(VTT) == ("Today we learn about fractions A fraction has a top and a bottom "
                              "The top is the numerator")


@pytest.mark.parametrize("url,ok", [("https://www.youtube.com/watch?v=abc123", True), ("https://youtu.be/abc123", True),
                                    ("https://m.youtube.com/watch?v=x", True),
                                    ("https://evil.example/watch?v=abc", False), ("https://youtube.com.evil.io/x", False),
                                    ("ftp://youtube.com/x", False), ("https://www.youtube.com/watch?v=a;rm -rf", False)])
def test_only_youtube_addresses_are_handed_to_the_tool(url, ok):
    if ok:
        assert valid_video_url(url) == url
    else:
        with pytest.raises(ValueError):
            valid_video_url(url)


async def test_captions_come_from_ytdlp_when_it_is_installed(tmp_path):
    seen = []

    async def run(cmd, cwd):
        seen.append(cmd)
        (cwd / "abc123.en.vtt").write_text(VTT)
        return 0, ""

    text = await fetch_captions("https://youtu.be/abc123", run, tmp_path, which=lambda n: "/usr/bin/yt-dlp")
    assert "numerator" in text and seen[0][0] == "/usr/bin/yt-dlp"
    assert "--skip-download" in seen[0] and "--no-playlist" in seen[0] and seen[0][-1] == "https://youtu.be/abc123"


async def test_without_ytdlp_it_says_so_and_offers_to_install_nothing_by_itself(tmp_path):
    async def run(cmd, cwd):
        raise AssertionError("must not run")

    with pytest.raises(CaptionsUnavailable, match="not installed"):
        await fetch_captions("https://youtu.be/abc123", run, tmp_path, which=lambda n: None)


async def test_a_video_without_captions_is_said_to_have_none(tmp_path):
    async def run(cmd, cwd):
        return 0, ""

    with pytest.raises(CaptionsUnavailable, match="no captions"):
        await fetch_captions("https://youtu.be/abc123", run, tmp_path, which=lambda n: "/x/yt-dlp")


# ---- sessions --------------------------------------------------------------------------------------------
def test_sessions_save_replace_list_and_restore(db):
    s = Sessions(db, clock=lambda: 1.0)
    s.save("homework", ["https://a.example/1", "https://b.example/2", "javascript:x", "https://a.example/1"])
    assert s.get("homework")["urls"] == ["https://a.example/1", "https://b.example/2"]
    s.save("homework", ["https://c.example/"])
    assert s.get("HOMEWORK")["urls"] == ["https://c.example/"]
    s.save("later", ["https://d.example/"])
    assert [x["name"] for x in s.list()] == ["homework", "later"] and s.latest()["name"] == "later"
    assert s.delete("later") and s.get("later") is None


def test_a_session_is_capped(db):
    s = Sessions(db)
    s.save("big", [f"https://x.example/{i}" for i in range(100)])
    assert len(s.get("big")["urls"]) == 40


# ---- form log --------------------------------------------------------------------------------------------
def test_the_form_log_keeps_field_and_site_never_the_value(db):
    log = FormLog(db, clock=lambda: 10.0)
    log.record("Shop.Example", "email")
    log.record("shop.example", "phone")
    log.record("shop.example", "email")
    seen = log.seen("shop.example")
    assert {x["field"]: x["count"] for x in seen} == {"email": 2, "phone": 1}
    assert "secret@mail.com" not in str(db.db.execute("SELECT * FROM formlog").fetchall())
    assert log.sites() == [{"host": "shop.example", "fields": ["email", "phone"], "count": 3}]
    assert log.forget("shop.example") == 3 and log.sites() == []
    log.record("a.example", "name")
    assert log.forget_all() == 1
