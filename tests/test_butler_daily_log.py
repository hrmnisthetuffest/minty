"""1066: a short list of everything done on your behalf today, standing approvals marked, with undo where possible."""
from datetime import datetime, timedelta

import pytest

from minty_core.butler.daily_log import render
from minty_core.butler.kit import ButlerProvider
from minty_core.config import Config
from minty_core.receipts import ReceiptStore

DAY = datetime(2026, 10, 15, 0, 0)


class Clock:
    def __init__(self, at):
        self.t = at.timestamp()

    def __call__(self):
        return self.t


@pytest.fixture
def receipts(tmp_path):
    clock = Clock(DAY + timedelta(hours=9))
    store = ReceiptStore(tmp_path / "r.sqlite", clock=clock)
    store.record("butler_add", 1, "butler add with domain chores, name Water the plants",
                 request="add water the plants to my chores", outcome="ok")
    clock.t += 3600
    store.record("scenes_launch", 2, "scenes launch with name study", outcome="ok",
                 detail="standing approval #1")
    clock.t += 600
    store.record("mail_send", 3, "mail send with to a@example.com", outcome="declined")
    store.record("files_delete", 3, "files delete with path x", outcome="blocked", detail="a safety rule")
    store.record("vault_note", 1, "vault note with title x", outcome="failed", detail="Error: x")
    return store, clock


def test_the_log_lists_what_was_done_with_time_request_and_standing_approvals(receipts):
    store, clock = receipts
    text = render(store.since((DAY).timestamp()), "today")
    assert text.startswith("What I did for you today (2 changes):")
    assert "09:00" in text and "Water the plants" in text and "you asked: add water the plants to my chores" in text
    assert "standing approval #1" in text
    assert "1 declined" in text and "1 blocked" in text and "1 failed" in text


def test_an_empty_day_says_so(receipts):
    store, clock = receipts
    assert render([], "today") == "I haven't done anything for you today."


def test_undo_hints_are_listed_when_there_are_some(receipts):
    store, _ = receipts
    text = render(store.since(DAY.timestamp()), "today",
                  undo=[{"id": 3, "label": "added Water the plants to chores"}])
    assert 'undo 3' in text and "Water the plants" in text


def test_the_voice_phrase_and_undo_phrase_work_through_the_provider(tmp_path, receipts):
    store, clock = receipts
    cfg = Config()
    cfg.butler.enabled = True
    provider = ButlerProvider(config=lambda: cfg, directory=tmp_path, clock=clock, receipts=store)
    b = provider.butler()
    item = b.add("chores", "Dishes")
    out = provider.capture("what did you do for me today")
    assert out.startswith("What I did for you today") and "undo" in out
    assert provider.capture("what did you do for me yesterday") == "I haven't done anything for you yesterday."
    (entry,) = [e for e in b.store.undo_entries() if e["action"] == "butler.delete"]
    assert provider.capture(f"undo {entry['id']}") == "Undone."
    assert b.list("chores") == [] and b.store.get(item.id) is None
    assert provider.capture("undo 999") == "I can't undo that one (it is already undone, or it is not undoable)."
    assert provider.capture("undo that") .startswith("There is nothing")


def test_phrases_do_nothing_when_the_butler_is_off(tmp_path, receipts):
    store, clock = receipts
    provider = ButlerProvider(config=lambda: Config(), directory=tmp_path, clock=clock, receipts=store)
    assert provider.capture("what did you do for me today") is None
