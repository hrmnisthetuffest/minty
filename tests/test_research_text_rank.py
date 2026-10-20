from datetime import date

from minty_core.research.rank import Hit, authority, canonical_url, select
from minty_core.research.text import chunk_text, html_to_text, normalise, sentences

PAGE = """<html><head><title> The  Water Cycle &amp; Climate </title><style>p{x}</style></head><body>
<nav>Home | About | Login</nav><header>Site banner</header>
<article><h1>The water cycle</h1><p>Water evaporates from oceans, condenses into clouds and falls as rain.
This cycle moves about 505,000 cubic kilometres of water every year.</p><p>Plants release vapour too.</p>
<script>var tracking = 1;</script></article><footer>Copyright</footer></body></html>"""


def test_readable_text_drops_chrome_and_keeps_the_article():
    title, text = html_to_text(PAGE + "<p>" + "filler words " * 5 + "</p>")
    assert title == "The Water Cycle & Climate"
    assert "Water evaporates from oceans" in text and "505,000 cubic kilometres" in text
    assert "Login" not in text and "tracking" not in text and "Copyright" not in text


def test_a_page_without_article_falls_back_to_the_body():
    _, text = html_to_text("<html><body><div>First paragraph here.</div><div>Second one.</div></body></html>")
    assert text.splitlines() == ["First paragraph here.", "Second one."]


def test_chunks_are_whole_sentences_with_overlap_and_lose_nothing():
    text = " ".join(f"Sentence number {i} says something useful about topic {i}." for i in range(80))
    chunks = chunk_text(text, size=500, overlap=60)
    assert len(chunks) > 5 and all(len(c) <= 560 for c in chunks)
    assert all(c.endswith(".") for c in chunks[:-1])
    joined = " ".join(chunks)
    assert all(f"Sentence number {i} says" in joined for i in range(80))  # nothing dropped
    assert normalise("“Quoted” — text  here") == '"quoted" - text here'
    assert sentences("Short. " + "A long enough factual sentence about the topic at hand. " * 2)[0].startswith("A long")


def test_canonical_urls_dedupe_tracking_and_www():
    a = canonical_url("http://www.Example.com/Page/?utm_source=x&id=7#top")
    b = canonical_url("https://example.com/Page?id=7&fbclid=abc")
    assert a == b == "https://example.com/Page?id=7"


def test_authority_orders_sources_and_honours_lists():
    assert authority("https://www.nih.gov/x") > authority("https://en.wikipedia.org/wiki/X") \
        > authority("https://someblog.example/x") > authority("https://www.pinterest.com/pin/1")
    assert authority("https://uni.edu/paper") >= 0.85 and authority("https://evil.example", deny={"evil.example"}) == 0
    assert authority("https://my-trusted.example/p", allow={"my-trusted.example"}) == 0.95


def test_selection_is_distinct_diverse_and_prefers_good_recent_sources():
    hits = [Hit(f"https://blog.example/post{i}", f"Water cycle explained part {i}", "water cycle evaporation",
                published="2024") for i in range(12)]
    hits += [Hit("https://www.nih.gov/water", "Water cycle and health", "water cycle evaporation clouds",
                 kind="reference", published="2022"),
             Hit("https://en.wikipedia.org/wiki/Water_cycle", "Water cycle", "water cycle evaporation condensation",
                 kind="reference", published="2024"),
             Hit("https://arxiv.org/abs/1", "Global water cycle intensification", "water cycle", kind="academic",
                 published="2019"),
             Hit("https://www.pinterest.com/pin/9", "water cycle poster", "water cycle"),
             Hit("https://blog.example/post0?utm_source=x", "dup", "water cycle")]
    chosen = select(hits, 6, "how does the water cycle work", today=date(2026, 10, 12))
    urls = [h.url for h in chosen]
    assert len(urls) == 6 and len({canonical_url(u) for u in urls}) == 6
    assert "https://www.nih.gov/water" in urls and "https://en.wikipedia.org/wiki/Water_cycle" in urls
    assert sum("blog.example" in u for u in urls) <= 3  # diversity: not six pages of one site
    assert not any("pinterest" in u for u in urls)
    only_farm = select(hits, 6, "water", deny={"blog.example", "nih.gov", "wikipedia.org", "arxiv.org"})
    assert [h.url for h in only_farm] == ["https://www.pinterest.com/pin/9"]  # a farm fills a place only when alone
