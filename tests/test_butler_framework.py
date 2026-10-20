"""The butler framework (LF-17): the store, the date phrases, a domain as data, the safety suite every domain passes."""
import ast
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from minty_core.butler.domain import Butler, ButlerError, Domain
from minty_core.butler.domains.chores import CHORES
from minty_core.butler.store import ButlerStore
from minty_core.butler.when import parse_when

SRC = Path(__file__).resolve().parents[1] / "src" / "minty_core"
NOW = datetime(2026, 10, 15, 10, 0)  # a Thursday


class Clock:
    def __init__(self, at=NOW):
        self.t = at.timestamp()

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw).total_seconds()


def at(text):
    due, every = parse_when(text, NOW)
    return (datetime.fromtimestamp(due).strftime("%Y-%m-%d %H:%M") if due else None), every


# ---- dates ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("tomorrow", ("2026-10-16 09:00", None)),
    ("today", ("2026-10-15 09:00", None)),
    ("tonight", ("2026-10-15 20:00", None)),
    ("in 3 days", ("2026-10-18 10:00", None)),
    ("in 2 hours", ("2026-10-15 12:00", None)),
    ("friday", ("2026-10-16 09:00", None)),
    ("on thursday", ("2026-10-22 09:00", None)),  # today is Thursday: the next one
    ("tomorrow at 5 pm", ("2026-10-16 17:00", None)),
    ("at 17:30", ("2026-10-15 17:30", None)),
    ("every 3 days", ("2026-10-18 09:00", 3)),
    ("every week", ("2026-10-22 09:00", 7)),
    ("every monday", ("2026-10-19 09:00", 7)),
    ("every 2 weeks", ("2026-10-29 09:00", 14)),
    ("20 Oct", ("2026-10-20 09:00", None)),
    ("2026-11-01", ("2026-11-01 09:00", None)),
    ("whenever", (None, None)),
    ("", (None, None)),
])
def test_when_phrases(text, expected):
    assert at(text) == expected


# ---- the store --------------------------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path):
    return ButlerStore(tmp_path / "butler.db", clock=Clock())


def test_items_are_stored_listed_and_found_by_words(store):
    a = store.add("chores", "Water the plants", due=NOW.timestamp() + 3600)
    store.add("chores", "Take out the bins")
    store.add("waiting", "Ravi: the form")
    assert [i.name for i in store.list("chores")] == ["Water the plants", "Take out the bins"]  # due first
    assert [i.id for i in store.find("chores", "plants")] == [a.id]
    assert store.list("nothing") == []


def test_the_file_is_owner_only(tmp_path):
    ButlerStore(tmp_path / "b.db").add("chores", "x")
    assert stat.S_IMODE(os.stat(tmp_path / "b.db").st_mode) == 0o600


def test_a_repeating_item_comes_back_after_done_and_a_single_one_closes(store):
    clock = store._clock
    r = store.add("chores", "Plants", due=clock() + 60, every_days=3)
    s = store.add("chores", "Bins", due=clock() + 60)
    clock.advance(minutes=5)
    r2, s2 = store.complete(r.id), store.complete(s.id)
    assert r2.status == "open" and r2.due == pytest.approx(r.due + 3 * 86400)
    assert s2.status == "done"
    assert [i.name for i in store.list("chores")] == ["Plants"]
    assert [i.name for i in store.list("chores", status="done")] == ["Bins"]


def test_due_items_are_notified_once_a_day_not_every_minute(store):
    clock = store._clock
    store.add("chores", "Plants", due=clock() - 10)
    assert [i.name for i in store.due(clock())] == ["Plants"]
    store.mark_notified(store.list("chores")[0].id, clock())
    assert store.due(clock()) == []
    clock.advance(hours=25)
    assert [i.name for i in store.due(clock())] == ["Plants"]


def test_export_and_delete_are_scoped_to_the_domain(store):
    store.add("chores", "Plants")
    store.add("waiting", "Ravi: form")
    exported = store.export("chores")
    assert [e["name"] for e in exported] == ["Plants"] and "waiting" not in json.dumps(exported)
    assert store.delete_domain("chores") == 1
    assert store.list("chores") == [] and len(store.list("waiting")) == 1


def test_undo_journal_restores_a_completed_repeating_item(store):
    clock = store._clock
    r = store.add("chores", "Plants", due=clock() - 5, every_days=2)
    store.complete(r.id)
    (entry,) = store.undo_entries()
    assert store.apply_undo(entry["id"]) is True
    back = store.get(r.id)
    assert back.due == pytest.approx(r.due) and store.undo_entries() == []


# ---- a domain is data --------------------------------------------------------------------------------------
@pytest.fixture
def butler(tmp_path):
    clock = Clock()
    return Butler(ButlerStore(tmp_path / "butler.db", clock=clock), {"chores": CHORES}, clock=clock)


def test_chores_are_added_listed_and_ticked_off_by_voice(butler):
    assert "Added" in butler.capture("add water the plants to my chores every 3 days")
    out = butler.capture("what are my chores")
    assert "water the plants" in out.lower()
    done = butler.capture("I did the plants")
    assert "water the plants" in done.lower() and "again" in done  # it repeats: next date is said
    assert butler.capture("I did the dishes") is None  # nothing matches: not ours, falls through


def test_a_phrase_that_is_not_butler_business_falls_through(butler):
    assert butler.capture("what is the weather like") is None
    assert butler.capture("add milk to my shopping list") is None  # not a domain we have


def test_it_refuses_id_numbers_and_empty_names(butler):
    with pytest.raises(ButlerError):
        butler.add("chores", "renew passport 1234 5678 9012")
    with pytest.raises(ButlerError):
        butler.add("chores", "   ")
    with pytest.raises(ButlerError):
        butler.add("nope", "x")


def test_a_domain_that_needs_a_date_asks_for_one(tmp_path):
    clock = Clock()
    d = Domain("waiting", "waiting-for list", noun="follow-up", needs_due=True, aliases=("waiting",))
    b = Butler(ButlerStore(tmp_path / "b.db", clock=clock), {"waiting": d}, clock=clock)
    with pytest.raises(ButlerError, match="date"):
        b.add("waiting", "Ravi: the form")
    assert b.add("waiting", "Ravi: the form", when="friday").due is not None


def test_notices_say_what_is_due_and_names_come_from_the_domain(butler):
    clock = butler._clock
    butler.add("chores", "Water the plants", when="in 1 hours")
    assert butler.notices(clock()) == []
    clock.advance(hours=2)
    (n,) = butler.notices(clock())
    assert n.text == "Chore time: Water the plants." and n.domain == "chores"


def test_butler_export_and_delete_through_the_service(butler):
    butler.add("chores", "Plants")
    assert json.loads(butler.export("chores"))[0]["name"] == "Plants"
    assert butler.delete("chores", "all") == 1 and butler.list("chores") == []


# ---- the safety suite every domain passes ------------------------------------------------------------------
def test_chores_is_a_sample_domain_in_under_one_hundred_lines():
    lines = (SRC / "butler" / "domains" / "chores.py").read_text().splitlines()
    assert len(lines) < 100


def test_no_butler_code_can_reach_the_network_or_run_a_program():
    banned = {"httpx", "requests", "urllib", "socket", "aiohttp", "smtplib", "subprocess", "webbrowser", "http"}
    for path in sorted((SRC / "butler").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                assert n.split(".")[0] not in banned, f"{path.name} imports {n}"
