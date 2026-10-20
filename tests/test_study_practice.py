"""722 past-paper practice, 723 mistake log with drills, 586 weekly review."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from minty_core.study.cards import Cards
from minty_core.study.db import StudyDB
from minty_core.study.fsrs import GOOD
from minty_core.study.practice import Mistakes, Papers, weekly_review

T0 = datetime(2026, 10, 15, 16, 0).timestamp()
PAPER = """Section A
1. State Newton's first law of motion. [2 marks]
2) A car of mass 800 kg accelerates at 2 m/s^2. Find the force. (3 marks)
   Show your working.
Q3. Define refraction of light. [2 marks]
4. Explain why the sky is blue. (4 marks)
"""


@pytest.fixture
def db(tmp_path):
    return StudyDB(tmp_path / "study.db")


def test_a_pasted_paper_becomes_questions_with_marks_and_continuations(db):
    papers = Papers(db, clock=lambda: T0)
    assert papers.import_text("Physics", PAPER, source="Term 1 paper", year="2025") == 4
    qs = papers.questions("Physics")
    assert [q["marks"] for q in qs] == [2, 3, 2, 4]
    assert qs[1]["text"].startswith("A car of mass 800 kg") and "Show your working." in qs[1]["text"]
    assert papers.import_text("Physics", PAPER) == 0  # the same paper is not added twice


def test_practice_fills_the_time_box_with_unasked_then_weakest_questions(db):
    papers = Papers(db, clock=lambda: T0)
    papers.import_text("Physics", PAPER)
    first = papers.practice("Physics", minutes=10)
    assert 1 <= len(first) <= 3 and sum(max(q["marks"], 1) * 1.5 for q in first) <= 10 + 1.5
    for q in first:
        papers.mark(q["id"], got_it=False)
    again = papers.practice("Physics", minutes=60)
    unasked = [q for q in again if q["asked"] == 0]
    asked = [q for q in again if q["asked"] > 0]
    assert unasked and asked and again.index(unasked[0]) < again.index(asked[0])  # fresh questions come first
    assert {q["id"] for q in again} == {q["id"] for q in papers.questions("Physics")}  # 60 minutes holds them all


def test_marks_update_the_score_per_subject(db):
    papers = Papers(db, clock=lambda: T0)
    papers.import_text("Physics", PAPER)
    qs = papers.questions("Physics")
    papers.mark(qs[0]["id"], got_it=True)
    papers.mark(qs[1]["id"], got_it=False)
    st = papers.status("Physics")
    assert st["questions"] == 4 and st["asked"] == 2 and st["correct"] == 1


def test_no_paper_is_said_plainly(db):
    assert Papers(db).practice("Chemistry", 20) == []


# ---- 723 -------------------------------------------------------------------------------------------------
def test_a_mistake_becomes_a_card_and_repeats_raise_its_count(db):
    cards = Cards(db, clock=lambda: T0)
    m = Mistakes(db, cards, clock=lambda: T0)
    a = m.log("Maths", "fractions", "added 1/2 + 1/3 as 2/5", "1/2 + 1/3 = 5/6 (common denominator)")
    b = m.log("Maths", "fractions", "added 1/2 + 1/3 as 2/5", "1/2 + 1/3 = 5/6 (common denominator)")
    assert a == b
    row = m.list("Maths")[0]
    assert row["count"] == 2 and row["card_id"] is not None
    card = cards.get(row["card_id"])
    assert card["deck"] == "mistakes" and "2/5" in card["front"] and "5/6" in card["back"]


def test_weak_topics_rank_by_how_often_and_how_recently(db):
    t = {"now": T0}
    m = Mistakes(db, Cards(db, clock=lambda: t["now"]), clock=lambda: t["now"])
    for _ in range(3):
        m.log("Maths", "fractions", "slip A", "fix A")
    m.log("Maths", "algebra", "slip B", "fix B")
    m.log("Physics", "units", "slip C", "fix C")
    assert [w["topic"] for w in m.weak_topics("Maths")] == ["fractions", "algebra"]
    assert m.weak_topics()[0]["topic"] == "fractions"


def test_a_drill_serves_the_mistake_cards_and_schedules_them(db):
    t = {"now": T0}
    cards = Cards(db, clock=lambda: t["now"])
    m = Mistakes(db, cards, clock=lambda: t["now"])
    m.log("Maths", "fractions", "slip A", "fix A")
    m.log("Maths", "algebra", "slip B", "fix B")
    drill = m.drill("Maths", n=5)
    assert len(drill) == 2 and {d["deck"] for d in drill} == {"mistakes"}
    cards.review(drill[0]["id"], GOOD, t["now"])
    assert len(m.drill("Maths", n=5)) == 1  # the reviewed one is not due again yet


# ---- 586 -------------------------------------------------------------------------------------------------
def test_the_weekly_review_counts_what_happened_and_what_is_next(db):
    t = {"now": T0}
    cards = Cards(db, clock=lambda: t["now"])
    papers = Papers(db, clock=lambda: t["now"])
    m = Mistakes(db, cards, clock=lambda: t["now"])
    i = cards.add("physics", "What is g?", "9.8 m/s^2")
    cards.review(i, GOOD, T0 - 2 * 86400)
    cards.review(i, GOOD, T0 - 86400)
    papers.import_text("Physics", PAPER)
    papers.mark(papers.questions("Physics")[0]["id"], got_it=True)
    m.log("Maths", "fractions", "slip A", "fix A")
    today = date(2026, 10, 15)
    exams = [("Maths", today + timedelta(days=6), ["algebra"]), ("Physics", today + timedelta(days=30), [])]
    homework = [SimpleNamespace(subject="Maths", task="Ex 4.2", due=today + timedelta(days=1)),
                SimpleNamespace(subject="English", task="Essay", due=today - timedelta(days=2))]
    receipts = [SimpleNamespace(tool="school_mark_done"), SimpleNamespace(tool="school_mark_done"),
                SimpleNamespace(tool="timers_set_timer")]
    text = weekly_review(cards, papers, m, exams, homework, receipts, today, T0)
    assert "2 flashcard reviews" in text and "1 practice question" in text and "1 mistake" in text
    assert "2 homework items ticked off" in text
    assert "Maths in 6 days" in text and "Physics in" not in text  # only the next two weeks
    assert "overdue" in text.lower() and "English" in text
