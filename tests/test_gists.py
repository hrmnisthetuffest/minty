import time
from datetime import datetime

import pytest

from minty_core.gists import GistIndex, add_summary, backfill, categorize, split_claims, words
from minty_core.history import ConversationArchive

DAY = 86400.0


@pytest.fixture
def idx(tmp_path):
    return GistIndex(tmp_path / "m.db", clock=lambda: 2_000_000_000.0)


def test_search_finds_by_stem_and_ranks_relevance_first(idx):
    a = idx.add("day", "Maths exercise 4 on fractions is due Friday", source_ref="d1")
    idx.add("day", "Bought a blue desk lamp from amazon for 799 rupees", source_ref="d2")
    idx.add("day", "Likes lo-fi playlists while studying maths", source_ref="d3")
    hits = idx.search("when are the maths exercises due")
    assert hits[0].id == a and len(hits) == 2  # the lamp does not match at all
    assert idx.search("???  !!!") == [] and idx.search("") == []


def test_fts_syntax_in_user_words_is_harmless(idx):
    idx.add("day", "Dentist appointment on Tuesday at five", source_ref="d")
    for q in ['dentist" OR 1=1 --', "NEAR(a b)", "dentist*", "col:dentist", "(((", "dentist AND"]:
        idx.search(q)  # must not raise
    assert idx.search('dentist" OR x')[0].gist.startswith("Dentist")


def test_importance_recency_and_usefulness_break_ties(tmp_path):
    now = [2_000_000_000.0]
    idx = GistIndex(tmp_path / "m.db", clock=lambda: now[0])
    old = idx.add("day", "Chemistry test syllabus covers acids", created=now[0] - 200 * DAY, source_ref="a")
    new = idx.add("day", "Chemistry test syllabus covers bases", created=now[0] - 2 * DAY, source_ref="b")
    assert [g.id for g in idx.search("chemistry syllabus")] == [new, old]
    for _ in range(6):  # the old one keeps being useful, the new one keeps being ignored
        idx.mark([old, new], used={old})
    assert idx.search("chemistry syllabus")[0].id == old
    imp = idx.add("day", "Chemistry exam deadline is 14 October", source_ref="c", created=now[0] - 2 * DAY)
    assert idx.search("chemistry")[0].id == imp  # numbers and deadlines are written as more important


def test_same_gist_from_the_same_source_is_not_added_twice(idx):
    assert idx.add("day", "Prefers short answers", source_ref="d") is not None
    assert idx.add("day", "prefers   short answers", source_ref="d") is None
    assert idx.add("day", "Prefers short answers", source_ref="other") is not None
    assert idx.add("day", "tiny", source_ref="d") is None


def test_map_categories_and_private_are_kept_apart(idx):
    idx.add("day", "Maths homework is due Friday", source_ref="a")
    idx.add("day", "Science exam on Monday", source_ref="b")
    idx.add("day", "Doctor said to rest for two days", source_ref="c", private=True)
    idx.add("day", "Installed python on the new laptop", source_ref="d")
    assert idx.map() == "School 2 · Computers 1" and "Health 1" in idx.map(include_private=True)
    assert idx.search("doctor rest") == [] and len(idx.search("doctor rest", include_private=True)) == 1
    assert categorize("play my spotify playlist") == "Music" and categorize("blah") == "General"


def test_budget_keeps_the_best_lines_that_fit(idx):
    for i in range(30):
        idx.add("day", f"Maths worksheet number {i} about long division and fractions practice", source_ref=f"s{i}")
    found = idx.search("maths worksheet", limit=30)
    kept = idx.within_budget(found, 250)
    assert 1 <= len(kept) < len(found) and kept == found[: len(kept)]
    assert sum((len(g.gist) + 24) // 4 for g in kept) <= 250
    assert len(idx.within_budget(found, 1)) == 1  # always at least the best one


def test_split_claims_makes_atomic_short_lines():
    text = ("- Wants the maths homework reminder after school. Prefers short answers.\n"
            "- Bought a lamp.\n- " + "word " * 80 + "\n\n3. Dentist on Tuesday at 5 pm")
    claims = split_claims(text)
    assert claims[0] == "Wants the maths homework reminder after school."
    assert all(len(c) <= 160 for c in claims) and any(c.endswith("…") for c in claims)
    assert any("Bought a lamp." in c for c in claims)  # a tiny claim is joined, not lost
    assert claims[-1] == "Dentist on Tuesday at 5 pm"


def test_words_drop_fillers():
    assert words("What is the maths homework for tomorrow?") == ["maths", "homework", "tomorrow"]


def test_backfill_imports_existing_summaries_once_and_marks_private_days(tmp_path):
    archive = ConversationArchive(tmp_path / "h")
    (archive.root / "days" / "2026-09-30.md").write_text("- Exam on Monday for science.\n- Likes jazz.\n")
    (archive.root / "months" / "2026-08.md").write_text("Worked on a Python project. Moved to Mint laptop.\n")
    (archive.root / "raw" / "2026-09-30.jsonl").write_text(
        '{"ts": 1, "role": "user", "text": "private thing", "private": true}\n')
    idx = GistIndex(tmp_path / "m.db")
    assert backfill(idx, archive) == 4
    assert backfill(idx, archive) == 0
    assert idx.search("science exam") == [] and len(idx.search("science exam", include_private=True)) == 1
    assert idx.search("python project")[0].kind == "month"


async def test_compaction_feeds_the_index(tmp_path):
    clock = [datetime(2026, 10, 12, 12).timestamp()]
    archive = ConversationArchive(tmp_path / "h", clock=lambda: clock[0])
    archive.log("user", "my maths exam is on Friday")
    archive.log("assistant", "noted")
    clock[0] += 2 * DAY
    idx = GistIndex(tmp_path / "m.db")

    async def summarize(instruction, text, limit):
        return "- The user has a maths exam on Friday.\n- They like short answers."

    got = []
    await archive.compact(summarize, sink=lambda k, l, s, p: got.append(add_summary(
        idx, k, l, s, created=clock[0], private=p, source_ref=f"{k}s/{l}")))
    assert got == [2] and idx.search("maths exam friday")[0].source_ref == "days/2026-10-12"
