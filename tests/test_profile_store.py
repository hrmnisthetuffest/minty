"""Answers are kept on this computer only, one small private file."""
import json
import stat

import pytest

from minty_core.profile import MAX_ANSWER, ProfileStore


def test_an_answer_is_saved_and_survives_a_restart(tmp_path):
    path = tmp_path / "profile" / "answers.json"
    store = ProfileStore(path)
    store.answer(1, "  Sam  ")
    assert store.get(1) == "Sam"
    assert ProfileStore(path).get(1) == "Sam"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_progress_counts_answers_and_skips_separately(tmp_path):
    store = ProfileStore(tmp_path / "a.json")
    store.answer(1, "x")
    store.answer(2, "y")
    store.skip(3)
    assert store.progress() == {"answered": 2, "skipped": 1, "total": 500}
    store.answer(3, "now answered")  # answering a skipped one takes it off the skipped list
    assert store.progress() == {"answered": 3, "skipped": 0, "total": 500}


def test_clear_removes_an_answer_and_a_skip(tmp_path):
    store = ProfileStore(tmp_path / "a.json")
    store.answer(5, "z")
    store.clear(5)
    store.skip(6)
    store.clear(6)
    assert store.get(5) is None and store.progress()["skipped"] == 0
    store.clear(7)  # nothing to clear is fine


def test_bad_input_is_refused(tmp_path):
    store = ProfileStore(tmp_path / "a.json")
    with pytest.raises(KeyError):
        store.answer(501, "x")
    with pytest.raises(KeyError):
        store.skip(0)
    for empty in ("", "   ", "\n"):
        with pytest.raises(ValueError):
            store.answer(1, empty)
    with pytest.raises(ValueError):
        store.answer(1, "x" * (MAX_ANSWER + 1))


def test_a_damaged_file_starts_empty_instead_of_failing(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("{not json")
    assert ProfileStore(path).progress()["answered"] == 0
    path.write_text(json.dumps({"answers": {"1": {"text": "ok", "at": 1}, "999": {"text": "no such question"}, "x": 5}}))
    store = ProfileStore(path)
    assert store.get(1) == "ok" and store.progress()["answered"] == 1


def test_rows_give_every_question_with_its_answer_and_state(tmp_path):
    store = ProfileStore(tmp_path / "a.json")
    store.answer(2, "an answer")
    store.skip(3)
    rows = store.rows()
    assert len(rows) == 500
    assert rows[1] == {"id": 2, "category": "about", "text": rows[1]["text"], "answer": "an answer", "skipped": False}
    assert rows[2]["skipped"] is True and rows[2]["answer"] == "" and rows[0]["answer"] == ""


def test_the_next_open_question_follows_the_order_and_wraps(tmp_path):
    store = ProfileStore(tmp_path / "a.json")
    for i in (1, 2, 4):
        store.answer(i, "a")
    store.skip(3)
    assert store.next_open() == 5  # skipped ones wait until nothing else is left
    assert store.next_open(after=5) == 6
    assert store.next_open(category="home") == 36
    for i in range(1, 501):
        if i != 3:
            store.answer(i, "a")
    assert store.next_open() == 3
    store.answer(3, "a")
    assert store.next_open() is None


def test_the_answers_export_as_readable_text(tmp_path):
    store = ProfileStore(tmp_path / "a.json")
    store.answer(1, "Sam")
    text = store.export_markdown()
    assert text.startswith("# Minty profile") and "## About you" in text and "What name should I call you?" in text and "Sam" in text
    assert "## Home" not in text  # a category with no answers is left out
