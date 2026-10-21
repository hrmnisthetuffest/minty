"""Bringing old free-form profile notes into the 500 questions: a proposal first, and only confident matches are written."""
from minty_core.profile import ProfileStore, QUESTIONS
from minty_core.profile_import import match_entry, parse_notes, run_import

NOTES = """# My old profile (private)

## Basics
- Q1 Name: full name Alex Example. Address me as "sir".
- Q2 Location: Springfield, a small city in the north
  with cold winters.
- Q3 Wake time: 6:30 on weekdays
- Q4 Allergies: peanuts and dust
- Preference: no lectures, keep answers short
- Favourite colour: green
- Something odd with no colon in it at all

## Food
- Diet: vegetarian, no eggs
"""


def qid(fragment):
    return next(q.id for q in QUESTIONS if fragment in q.text)


def test_notes_are_split_into_labelled_entries_with_wrapped_lines_joined():
    entries = parse_notes(NOTES)
    labels = [e.label for e in entries]
    assert labels[:4] == ["Name", "Location", "Wake time", "Allergies"] and "Diet" in labels
    location = next(e for e in entries if e.label == "Location")
    assert location.text == "Springfield, a small city in the north with cold winters."
    assert not any("no colon" in e.label for e in entries)  # a line with no label is not an entry


def test_an_entry_goes_to_the_question_it_is_about():
    entries = {e.label: e for e in parse_notes(NOTES)}
    assert match_entry(entries["Allergies"]).id == qid("Do you have any allergies")
    assert match_entry(entries["Diet"]).id == qid("Do you follow any diet")
    assert match_entry(entries["Favourite colour"]).id == qid("What is your favourite colour")
    assert match_entry(entries["Wake time"]).id == qid("What time do you usually wake up on a weekday")


def test_a_vague_entry_is_left_unmatched_rather_than_guessed():
    entries = {e.label: e for e in parse_notes(NOTES)}
    assert match_entry(entries["Preference"]) is None


def test_the_proposal_is_a_dry_run_and_apply_writes_only_matches_that_are_still_empty(tmp_path):
    store = ProfileStore(tmp_path / "a.json")
    store.answer(qid("Do you have any allergies"), "already answered")
    report = run_import(NOTES, store, apply=False)
    assert report["matched"] and report["unmatched"] and store.progress()["answered"] == 1  # nothing written
    wrote = run_import(NOTES, store, apply=True)
    assert store.get(qid("Do you have any allergies")) == "already answered"  # never overwritten
    assert store.get(qid("Do you follow any diet")) == "vegetarian, no eggs"
    assert wrote["written"] >= 2 and wrote["skipped_existing"] == 1


def test_two_entries_for_one_question_keep_the_first(tmp_path):
    store = ProfileStore(tmp_path / "a.json")
    text = "- Diet: vegetarian\n- Diet: vegan\n"
    result = run_import(text, store, apply=True)
    assert store.get(qid("Do you follow any diet")) == "vegetarian" and result["written"] == 1


def test_long_answers_are_trimmed_to_what_the_profile_keeps(tmp_path):
    store = ProfileStore(tmp_path / "a.json")
    run_import("- Diet: " + "plants " * 200 + "\n", store, apply=True)
    assert 0 < len(store.get(qid("Do you follow any diet"))) <= 600


def test_the_command_exists_and_reads_a_file(tmp_path, monkeypatch, capsys):
    from minty_core.app import parse_args
    args = parse_args(["profile-import", "notes.md", "--apply"])
    assert args.command == "profile-import" and args.path == "notes.md" and args.apply is True
    assert parse_args(["profile-import", "notes.md"]).apply is False


def test_a_label_that_fits_two_questions_equally_is_not_guessed():
    from minty_core.profile_import import Entry
    assert match_entry(Entry("Wake time", "6:30")) is None  # a weekday question and a weekend question both fit
    assert match_entry(Entry("Wake time", "6:30, later at weekends")).text.endswith("at the weekend?")  # unless the answer says which
