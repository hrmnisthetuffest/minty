"""578 flashcards with FSRS: deterministic, restart-safe, voice-graded."""
import math

import pytest

from minty_core.study.cards import Cards, grade_from_text
from minty_core.study.db import StudyDB
from minty_core.study.fsrs import AGAIN, EASY, GOOD, HARD, Scheduler

DAY = 86400.0
T0 = 1_800_000_000.0


def test_retrievability_is_ninety_percent_at_the_stability_and_falls_after():
    s = Scheduler()
    assert s.retrievability(5.0, 5.0) == pytest.approx(0.9, abs=1e-6)
    assert s.retrievability(10.0, 5.0) < 0.9 < s.retrievability(1.0, 5.0)
    assert s.interval_days(5.0) == 5


def test_first_reviews_use_the_default_initial_stabilities():
    s = Scheduler()
    assert s.review(None, GOOD, T0).due == pytest.approx(T0 + 4 * DAY)  # S = 3.71 days -> 4
    assert s.review(None, EASY, T0).due == pytest.approx(T0 + 14 * DAY)
    assert s.review(None, AGAIN, T0).due == pytest.approx(T0 + 10 * 60)  # relearn in ten minutes
    assert s.review(None, HARD, T0).due == pytest.approx(T0 + 1 * DAY)


def test_good_reviews_on_time_grow_the_interval_each_time():
    s = Scheduler()
    c, now, gaps = None, T0, []
    for _ in range(6):
        c = s.review(c, GOOD, now)
        gaps.append(round((c.due - now) / DAY))
        now = c.due
    assert gaps == sorted(gaps) and gaps[-1] > gaps[0] * 4
    assert c.reps == 6 and c.lapses == 0 and 1 <= c.difficulty <= 10


def test_hard_good_easy_order_the_next_interval_and_again_lapses():
    s = Scheduler()
    base = s.review(None, GOOD, T0)
    when = base.due
    nxt = {g: s.review(base, g, when) for g in (HARD, GOOD, EASY)}
    assert (nxt[HARD].due - when) < (nxt[GOOD].due - when) < (nxt[EASY].due - when)
    lapsed = s.review(base, AGAIN, when)
    assert lapsed.lapses == 1 and lapsed.stability < base.stability and lapsed.state == "relearning"
    assert lapsed.due == pytest.approx(when + 10 * 60)


def test_difficulty_stays_between_one_and_ten():
    s = Scheduler()
    c, now = None, T0
    for _ in range(30):
        c = s.review(c, AGAIN, now)
        now = c.due
    assert c.difficulty == 10.0
    for _ in range(30):
        c = s.review(c, EASY, now)
        now = c.due
    assert 1.0 <= c.difficulty < 10.0


def test_a_late_review_earns_more_stability_than_an_on_time_one():
    s = Scheduler()
    base = s.review(None, GOOD, T0)
    on_time = s.review(base, GOOD, base.due)
    late = s.review(base, GOOD, base.due + 10 * DAY)
    assert late.stability > on_time.stability


# ---- the card store ---------------------------------------------------------------------------------------
@pytest.fixture
def cards(tmp_path):
    return Cards(StudyDB(tmp_path / "study.db"), clock=lambda: T0)


def test_new_cards_come_first_up_to_the_daily_limit_then_due_cards(cards):
    ids = [cards.add("physics", f"What is {n}?", f"{n}") for n in "ABCDE"]
    got = [cards.next_card(now=T0, new_limit=2)["id"] for _ in range(1)]
    assert got == [ids[0]]
    cards.review(ids[0], GOOD, T0)
    cards.review(ids[1], GOOD, T0)
    # two new cards were started today, so no third until tomorrow; nothing is due yet
    assert cards.next_card(now=T0, new_limit=2) is None
    assert cards.next_card(now=T0 + 5 * DAY, new_limit=2)["id"] == ids[0]  # due again
    assert cards.stats()["new"] == 3


def test_state_survives_a_restart_and_due_cards_are_deterministic(tmp_path):
    db = StudyDB(tmp_path / "study.db")
    a = Cards(db, clock=lambda: T0)
    i = a.add("maths", "7 x 8", "56")
    a.review(i, GOOD, T0)
    a.review(i, GOOD, T0 + 4 * DAY)
    b = Cards(StudyDB(tmp_path / "study.db"), clock=lambda: T0)
    c = b.get(i)
    assert c["reps"] == 2 and c["due"] == a.get(i)["due"]
    assert [x["id"] for x in b.due(c["due"])] == [i] and b.due(c["due"] - 1) == []


def test_the_same_card_is_not_added_twice_and_empty_cards_are_refused(cards):
    a = cards.add("maths", "7 x 8", "56")
    assert cards.add("maths", "7 X 8 ", "56") == a
    with pytest.raises(ValueError):
        cards.add("maths", "  ", "x")


@pytest.mark.parametrize("said,grade", [("got it", GOOD), ("easy", EASY), ("I knew that", GOOD), ("hard", HARD),
                                        ("barely", HARD), ("no", AGAIN), ("wrong", AGAIN), ("forgot", AGAIN),
                                        ("I don't know", AGAIN), ("blah", None)])
def test_voice_grades(said, grade):
    assert grade_from_text(said) == grade


def test_a_spoken_answer_that_matches_is_good_and_a_miss_is_shown_the_answer(cards):
    i = cards.add("geo", "Capital of France?", "Paris")
    assert cards.check_answer(i, "it's paris") == (GOOD, True)
    assert cards.check_answer(i, "Lyon") == (AGAIN, False)
    j = cards.add("maths", "7 x 8", "56")
    assert cards.check_answer(j, "fifty six")[1] is True  # number words
    assert cards.check_answer(j, "54")[1] is False


def test_review_log_counts_today(cards):
    i = cards.add("maths", "7 x 8", "56")
    cards.review(i, GOOD, T0)
    cards.review(i, AGAIN, T0 + 3 * 86400 + 5)
    assert cards.reviews_between(T0 - 1, T0 + 10 * 86400) == 2
