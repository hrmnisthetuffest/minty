"""The 500 fixed profile questions: always the same 500, in the same order, with the same numbers."""
import re
from collections import Counter

from minty_core.profile import CATEGORIES, QUESTIONS, question

EXPECTED = {"about": 35, "home": 30, "routine": 40, "health": 45, "fitness": 40, "food": 45, "sleep": 20, "work": 45,
            "interests": 50, "tech": 35, "assistant": 25, "people": 25, "money": 20, "goals": 25, "limits": 20}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def test_there_are_exactly_five_hundred_questions_numbered_one_to_five_hundred():
    assert len(QUESTIONS) == 500
    assert [q.id for q in QUESTIONS] == list(range(1, 501))


def test_the_categories_and_their_sizes_are_fixed():
    assert dict(Counter(q.category for q in QUESTIONS)) == EXPECTED
    assert [c.id for c in CATEGORIES] == list(EXPECTED)
    assert all(c.title and c.blurb for c in CATEGORIES)


def test_questions_of_a_category_sit_together_in_the_category_order():
    order = [c.id for c in CATEGORIES]
    seen = [q.category for q in QUESTIONS]
    assert seen == sorted(seen, key=order.index)


def test_every_question_is_a_distinct_plain_question():
    texts = [q.text for q in QUESTIONS]
    assert len({norm(t) for t in texts}) == 500
    for q in QUESTIONS:
        assert q.text.endswith("?") and 12 <= len(q.text) <= 170, q
        assert q.text == q.text.strip() and "  " not in q.text and "\n" not in q.text, q


def test_lookup_by_number():
    assert question(1).text.startswith("What name should I call you")
    assert question(500).category == "limits"
    for bad in (0, 501, -1):
        try:
            question(bad)
        except KeyError:
            continue
        raise AssertionError(bad)


def test_no_old_or_personal_names_in_the_bank():
    blob = " ".join(q.text for q in QUESTIONS).lower()
    for word in ("ha" + "des", "ul" + "tron", "har" + "man"):  # the old project names and the owner's name, written so this file holds none
        assert word not in blob
