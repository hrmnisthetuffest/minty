"""658 news brief, 659 arXiv watcher, 660 idea inbox review, 661 mind maps."""
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest

from minty_core.create.db import CreateDB
from minty_core.create.feeds import News, dedupe, parse_feed
from minty_core.create.inbox import weekly_review
from minty_core.create.mindmap import parse_outline, to_mermaid, to_svg
from minty_core.create.papers import PaperWatch, parse_arxiv
from minty_core.dev.ideas import Ideas

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Daily Science</title>
<item><title>Fusion record set in Germany</title><link>https://sci.example/a?utm_source=x&amp;id=1</link>
<pubDate>Sun, 18 Oct 2026 09:00:00 +0000</pubDate><description>&lt;p&gt;Researchers held plasma for 43 seconds.&lt;/p&gt;</description></item>
<item><title>Old story</title><link>https://sci.example/old</link><pubDate>Mon, 01 Jun 2026 09:00:00 +0000</pubDate>
<description>x</description></item>
<item><title>Script &lt;script&gt;alert(1)&lt;/script&gt; title</title><link>javascript:alert(1)</link>
<pubDate>Sun, 18 Oct 2026 08:00:00 +0000</pubDate><description>bad link</description></item>
</channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Tech Wire</title>
<entry><title>Fusion record set in Germany!</title><link href="https://tech.example/fusion"/>
<updated>2026-10-18T10:30:00Z</updated><summary>Plasma held for 43 seconds at the Wendelstein stellarator.</summary></entry>
<entry><title>New Linux kernel released</title><link href="https://tech.example/kernel"/>
<updated>2026-10-17T20:00:00Z</updated><summary>Version 7.2 is out.</summary></entry></feed>"""


def test_rss_and_atom_parse_into_items_and_unsafe_links_and_markup_are_dropped():
    rss = parse_feed(RSS)
    assert [i.title for i in rss][:2] == ["Fusion record set in Germany", "Old story"]
    assert rss[0].source == "Daily Science" and rss[0].summary == "Researchers held plasma for 43 seconds."
    assert all(i.link.startswith("https://") for i in rss) and len(rss) == 2  # the javascript: link item is gone
    atom = parse_feed(ATOM)
    assert [i.source for i in atom] == ["Tech Wire", "Tech Wire"] and atom[0].link == "https://tech.example/fusion"
    assert atom[0].published == datetime(2026, 10, 18, 10, 30, tzinfo=timezone.utc)


def test_entity_tricks_and_garbage_are_refused():
    assert parse_feed('<!DOCTYPE x [<!ENTITY a "b">]><rss><channel><item><title>&a;</title></item></channel></rss>') == []
    assert parse_feed("not xml") == [] and parse_feed("") == []


def test_the_same_story_from_two_outlets_is_one_item_that_names_both():
    items = dedupe(parse_feed(RSS) + parse_feed(ATOM))
    fusion = [i for i in items if "Fusion" in i.title]
    assert len(fusion) == 1 and sorted(fusion[0].also) == ["Tech Wire"] and fusion[0].source == "Daily Science"
    assert len(items) == 3  # fusion, old story, kernel


class Net:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def fetch(self, url):
        self.calls.append(url)
        if isinstance(self.pages[url], Exception):
            raise self.pages[url]
        return self.pages[url]


async def test_the_brief_cites_each_outlet_is_recent_and_polite(tmp_path):
    net = Net({"https://sci.example/feed": RSS, "https://tech.example/feed": ATOM})
    t = [NOW.timestamp()]
    news = News(net.fetch, CreateDB(tmp_path / "c.db"), clock=lambda: t[0], min_gap_s=1800)
    text = await news.brief(["https://sci.example/feed", "https://tech.example/feed"], max_items=5, now=NOW)
    assert "Fusion record set in Germany" in text and "Daily Science" in text and "also Tech Wire" in text
    assert "Old story" not in text  # older than the window
    assert "https://tech.example/kernel" in text  # every item carries its link
    again = await news.brief(["https://sci.example/feed", "https://tech.example/feed"], max_items=5, now=NOW)
    assert len(net.calls) == 2 and again == text  # the second brief came from the cache
    t[0] += 3600
    await news.brief(["https://sci.example/feed"], now=NOW)
    assert len(net.calls) == 3  # an hour later it asks again


async def test_a_dead_feed_is_named_and_the_others_still_report(tmp_path):
    net = Net({"https://sci.example/feed": RuntimeError("boom"), "https://tech.example/feed": ATOM})
    news = News(net.fetch, CreateDB(tmp_path / "c.db"), clock=lambda: NOW.timestamp())
    text = await news.brief(["https://sci.example/feed", "https://tech.example/feed"], now=NOW)
    assert "sci.example could not be read" in text and "New Linux kernel released" in text


async def test_no_feeds_says_how_to_add_one(tmp_path):
    news = News(Net({}).fetch, CreateDB(tmp_path / "c.db"))
    assert "No news feeds" in await news.brief([], now=NOW)


# ---- arXiv ----------------------------------------------------------------------------------------------------------
ARXIV = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2610.00001v1</id><published>2026-10-17T00:00:00Z</published>
<title>Sparse attention at scale</title><summary>We study sparse attention.</summary>
<author><name>A. Kumar</name></author><author><name>B. Rao</name></author></entry>
<entry><id>http://arxiv.org/abs/2610.00002v1</id><published>2026-10-16T00:00:00Z</published>
<title>Another paper</title><summary>x</summary><author><name>C. Devi</name></author></entry></feed>"""


def test_arxiv_entries_parse():
    got = parse_arxiv(ARXIV)
    assert got[0].id == "2610.00001" and got[0].title == "Sparse attention at scale" and got[0].authors == ["A. Kumar", "B. Rao"]
    assert got[0].url == "https://arxiv.org/abs/2610.00001"


async def test_the_watcher_baselines_then_reports_only_new_papers_and_never_asks_too_often(tmp_path):
    pages = {"v1": ARXIV}
    calls = []

    async def get(url, params):
        calls.append(params["search_query"])
        return pages["v1"]

    t = [0.0]
    w = PaperWatch(CreateDB(tmp_path / "c.db"), get, clock=lambda: t[0], min_gap_s=43200, sleep=lambda s: _nap())
    assert "Watching" in w.add("sparse attention")
    assert await w.check() == []  # first look: remembered silently
    t[0] += 3600
    assert await w.check() == [] and len(calls) == 1  # too soon: no request
    pages["v1"] = ARXIV.replace("</feed>", """<entry><id>http://arxiv.org/abs/2610.00003v1</id>
        <published>2026-10-18T00:00:00Z</published><title>Fresh result</title><summary>y</summary>
        <author><name>D. Rao</name></author></entry></feed>""")
    t[0] += 43200
    out = await w.check()
    assert len(out) == 1 and "Fresh result" in out[0] and "sparse attention" in out[0] and "arxiv.org/abs/2610.00003" in out[0]
    t[0] += 43200
    assert await w.check() == []  # not repeated
    assert w.topics() == ["sparse attention"] and w.remove("sparse attention") and w.topics() == []


async def _nap():
    return None


def test_a_topic_cannot_carry_an_arxiv_operator_injection(tmp_path):
    w = PaperWatch(CreateDB(tmp_path / "c.db"), lambda u, p: None)
    for bad in ("", "x" * 200, "ti:foo AND cat:cs.CR; rm", "<script>"):
        with pytest.raises(ValueError):
            w.add(bad)


# ---- idea inbox ----------------------------------------------------------------------------------------------------
def test_the_weekly_review_counts_what_came_went_and_went_stale(tmp_path):
    day = 86400.0
    now = 1_760_000_000.0
    ideas = Ideas(CreateDB(tmp_path / "i.db"), clock=lambda: now - 2 * day)
    ideas.add("a podcast about my school", kind="idea")
    ideas.add("learn the guitar chords", kind="idea")
    old = Ideas(ideas.d, clock=lambda: now - 40 * day)
    old.add("build a robot arm", kind="idea")
    old.add("write to the museum", kind="todo")
    ideas.done(2)
    text = weekly_review(ideas, now)
    assert "2 new this week" in text and "1 finished" in text
    assert "Still open from before: 2" in text and "build a robot arm" in text
    assert "Pick one" in text
    empty = weekly_review(Ideas(CreateDB(tmp_path / "e.db")), now)
    assert "Your idea inbox is empty" in empty


# ---- mind maps -----------------------------------------------------------------------------------------------------
OUTLINE = """Solar system
  Inner planets
    Mercury
    Venus
    Earth
      Moon
  Outer planets
    Jupiter
    Saturn
- Dwarf planets
  - Pluto (and Charon)
"""


def test_outlines_nest_by_indentation_and_bullets():
    root = parse_outline(OUTLINE)
    assert root.label == "Solar system"
    assert [c.label for c in root.children] == ["Inner planets", "Outer planets", "Dwarf planets"]
    earth = root.children[0].children[2]
    assert earth.label == "Earth" and [c.label for c in earth.children] == ["Moon"]


def test_mermaid_text_has_the_indentation_syntax_and_no_characters_that_break_it():
    text = to_mermaid(parse_outline(OUTLINE))
    lines = text.splitlines()
    assert lines[0] == "mindmap" and lines[1].strip().startswith("root((Solar system))")
    assert "      Earth" in text and "        Moon" in text
    assert "(and Charon)" not in text and "Pluto and Charon" in text


def test_the_svg_is_well_formed_script_free_and_keeps_everything_inside_the_picture():
    root = parse_outline(OUTLINE + "  <script>alert(1)</script>\n")
    svg = to_svg(root)
    tree = ET.fromstring(svg)
    assert tree.tag.endswith("svg") and "<script" not in svg.lower()
    w, h = [float(x) for x in tree.attrib["viewBox"].split()[2:]]
    for r in tree.iter("{http://www.w3.org/2000/svg}rect"):
        assert 0 <= float(r.attrib["x"]) and float(r.attrib["x"]) + float(r.attrib["width"]) <= w
        assert 0 <= float(r.attrib["y"]) and float(r.attrib["y"]) + float(r.attrib["height"]) <= h
    assert svg.count("<rect") == 12


def test_size_limits_and_empty_input():
    with pytest.raises(ValueError):
        parse_outline("   \n")
    big = "Root\n" + "\n".join(f"  node {i}" for i in range(300))
    assert len(list(parse_outline(big).walk())) <= 120
