"""607: a new fact that contradicts an old one raises a question; nothing is overwritten silently."""
import pytest

from minty_core.governance.contradiction import ContradictionStore, conflicts, extract, prompt_line


@pytest.mark.parametrize("old,new", [
    ("School bus leaves at 7:15", "School bus leaves at 7:30"),
    ("User lives in Paris", "User lives in Lyon"),
    ("My favourite colour is blue", "Favourite color is green"),
    ("Weighs 62 kg", "Weighs 64 kg"),
    ("Birthday is 12 March", "Birthday is 21 March"),
    ("Alex's exam is on 20 Nov", "The exam is on 25 Nov"),
])
def test_these_pairs_contradict(old, new):
    assert conflicts(new, [("fact:1", old)]) == [("fact:1", old)]


@pytest.mark.parametrize("old,new", [
    ("Likes mango lassi", "Likes tea"),
    ("Favourite colour is blue", "Favourite colour is blue and green"),
    ("School bus leaves at 7:15", "School bus leaves at 7:15"),
    ("School bus leaves at 7:15", "Pi camera arrives on Friday"),
    ("Weighs 62 kg", "Height is 170 cm"),
    ("User lives in Paris", "User studies in Paris"),
])
def test_these_pairs_do_not(old, new):
    assert conflicts(new, [("fact:1", old)]) == []


def test_extraction_normalises_spelling_possessives_and_times():
    claims = extract("Alex's favourite colour is Blue.")
    assert [(c.attr, c.value) for c in claims] == [("favorite color", "blue")]
    assert [(c.attr, c.value) for c in extract("School bus leaves at 7.15 am")] == [("school bus leave", "7:15 am")]


def test_a_question_is_raised_once_and_resolution_is_recorded(tmp_path):
    s = ContradictionStore(tmp_path / "c.json", clock=lambda: 1000.0)
    p = s.raise_("fact:1", "School bus leaves at 7:15", "School bus leaves at 7:30")
    again = s.raise_("fact:1", "School bus leaves at 7:15", "School bus leaves at 7:30")
    assert p.id == again.id and len(s.open()) == 1
    assert "7:15" in prompt_line(s) and "7:30" in prompt_line(s) and "memory_resolve" in prompt_line(s)
    done = ContradictionStore(tmp_path / "c.json").resolve(p.id, "new")
    assert done.status == "new" and s.open() == [] and prompt_line(s) == ""
    with pytest.raises(ValueError):
        s.resolve(p.id, "new")  # already answered
    with pytest.raises(ValueError):
        s.resolve(999, "new")
    q = s.raise_("fact:2", "a thing", "a different thing")
    with pytest.raises(ValueError):
        s.resolve(q.id, "maybe")


def test_an_answer_older_than_a_month_is_dropped_not_nagged(tmp_path):
    t = {"now": 1000.0}
    s = ContradictionStore(tmp_path / "c.json", clock=lambda: t["now"])
    s.raise_("fact:1", "a", "b")
    t["now"] += 31 * 86400
    assert s.open() == [] and prompt_line(s) == ""
