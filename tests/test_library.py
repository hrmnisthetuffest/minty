import time

from minty_core.research.library import Library, categorise, slugify

DAY = 86400.0


def save(lib, topic, claims, kind="web", created_ago_days=0, sources=None):
    now = time.time()
    lib._clock = lambda: now - created_ago_days * DAY
    srcs = sources or [{"id": "S1", "domain": "nih.gov", "kind": kind, "url": "https://nih.gov/a", "title": "A",
                        "chunks": []}]
    ref = lib.save(topic, "light", f"# {topic}\n", srcs,
                   [{"text": c, "quote": c + " (quote)", "source": "S1", "chunk": 0, "support": 1} for c in claims],
                   f"summary of {topic}", "medium")
    lib._clock = time.time
    return ref


def test_categories_are_stable_and_overridable(tmp_path):
    lib = Library(tmp_path / "lib")
    assert categorise("sleep and heart health in teenagers") == "Health"
    assert categorise("how to set up a linux server with python") == "Computers"
    assert categorise("zzz qqq") == "General"
    a = save(lib, "Sleep and teenagers", ["Teens need eight to ten hours of sleep"])
    b = save(lib, "Sleep and teenagers", ["Another note on the same topic"])
    assert (a.category, a.slug) == ("Health", "sleep-and-teenagers") and b.slug == "sleep-and-teenagers-2"
    assert lib.move("sleep-and-teenagers", "school").startswith("Moved") and lib.meta("sleep-and-teenagers")["category"] \
        == "School"
    assert lib.move("nope", "Health").startswith("Error") and lib.move("sleep-and-teenagers", "###").startswith("Error")
    assert slugify("  Hello, World!! ") == "hello-world"


def test_ask_answers_from_claims_and_flags_old_notes(tmp_path):
    lib = Library(tmp_path / "lib")
    save(lib, "Vitamin D", ["Vitamin D helps the body absorb calcium", "Most people get vitamin D from sunlight"],
         kind="news", created_ago_days=40)
    save(lib, "Python packaging", ["pip installs packages from the Python Package Index"])
    hits, metas = lib.ask("how does the body absorb calcium with vitamin d")
    assert hits[0]["claim"].startswith("Vitamin D helps") and all("Python" not in h["claim"] for h in hits)
    meta = metas[hits[0]["note"]]
    assert "out of date" in lib.freshness(meta, {"news"}) and lib.freshness(meta, {"academic"}) == ""
    assert lib.ask("zzzz") == ([], {}) and lib.ask("   ") == ([], {})


def test_disputed_claims_leave_answers_and_lower_the_source_weight(tmp_path):
    lib = Library(tmp_path / "lib")
    save(lib, "Moon", ["The moon is made of cheese according to a blog"],
         sources=[{"id": "S1", "domain": "cheesy.example", "kind": "web", "url": "u", "title": "t", "chunks": []}])
    hits, _ = lib.ask("moon cheese")
    assert lib.dispute(hits[0]["id"]) == "cheesy.example" and lib.dispute(999) == ""
    assert lib.ask("moon cheese")[0] == []
    assert lib.weights() == {"cheesy.example": 0.7}
