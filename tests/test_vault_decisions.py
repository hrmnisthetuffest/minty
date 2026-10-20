from datetime import date, datetime

import pytest

from minty_core.vault.bookmarks import Bookmarks, BookmarkError, normalise
from minty_core.vault.decisions import Decisions, weigh
from minty_core.vault.glossary import Glossary
from minty_core.vault.store import Vault


class Clock:
    def __init__(self):
        self.t = datetime.fromisoformat("2026-10-15T10:00:00").timestamp()

    def __call__(self):
        self.t += 60
        return self.t


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "brain", clock=Clock(), git=False)


# ---- 114 / 701 decision journal --------------------------------------------------------------------------
def test_a_decision_is_saved_with_why_options_and_a_revisit_date(vault):
    d = Decisions(vault).log("Use the Pi 5", decision="Buy the Pi 5 for the vision rig.", why="The Pi 4 is too slow.",
                             options=["Pi 4", "Pi 5"], revisit="2027-01-15", tags=["hardware"])
    assert d.path.startswith("decisions/") and "decision" in d.tags and "revisit:2027-01-15" in d.tags
    body = vault.get(d.id).body
    assert "Buy the Pi 5" in body and "The Pi 4 is too slow." in body and "- Pi 4" in body


def test_decisions_due_for_a_second_look(vault):
    dec = Decisions(vault)
    a = dec.log("Early", decision="x", revisit="2026-10-01")
    dec.log("Later", decision="x", revisit="2027-01-01")
    dec.log("Never", decision="x")
    assert [n.id for n in dec.due(date(2026, 10, 15))] == [a.id]


def test_a_bad_revisit_date_is_dropped_not_stored(vault):
    d = Decisions(vault).log("t", decision="x", revisit="next tuesday")
    assert not any(t.startswith("revisit:") for t in d.tags)


def test_the_helper_scores_options_and_is_deterministic():
    options = [
        {"name": "Pi 5", "pros": [{"text": "fast", "weight": 4}, {"text": "new", "weight": 1}],
         "cons": [{"text": "price", "weight": 3}]},
        {"name": "Pi 4", "pros": [{"text": "cheap", "weight": 3}], "cons": [{"text": "slow", "weight": 4}]},
    ]
    v = weigh(options)
    assert [(o.name, o.score) for o in v.ranked] == [("Pi 5", 2), ("Pi 4", -1)]
    assert v.leader == "Pi 5" and v.tie is False
    assert weigh(options).summary == v.summary


def test_a_tie_is_reported_as_a_tie_and_weights_are_clamped():
    v = weigh([{"name": "A", "pros": [{"text": "x", "weight": 99}], "cons": []},
               {"name": "B", "pros": [{"text": "y", "weight": 5}], "cons": []}])
    assert v.tie is True and v.leader == "" and v.ranked[0].score == 5
    assert "tie" in v.summary.lower()


def test_a_verdict_can_be_saved_to_the_journal_without_choosing_for_the_user(vault):
    dec = Decisions(vault)
    v = weigh([{"name": "A", "pros": [{"text": "x", "weight": 3}], "cons": []},
               {"name": "B", "pros": [], "cons": [{"text": "y", "weight": 2}]}])
    n = dec.save_verdict("Which board?", v)
    body = vault.get(n.id).body
    assert "A: +3" in body and "B: -2" in body and "You decide" in body
    assert "decision" in n.tags and n.confidence == "verified"


# ---- 617 bookmarks ---------------------------------------------------------------------------------------
def test_bookmarks_are_notes_in_sources_found_by_words(vault):
    b = Bookmarks(vault).add("https://www.raspberrypi.com/documentation/#top", title="Pi docs",
                             summary="Official docs for every board.", tags=["pi"])
    assert b.path.startswith("sources/") and "bookmark" in b.tags
    found = Bookmarks(vault).find("official docs")
    assert [n.id for n in found] == [b.id]
    assert "https://www.raspberrypi.com/documentation/" in vault.get(b.id).body


def test_the_same_page_is_saved_once(vault):
    bm = Bookmarks(vault)
    a = bm.add("https://example.org/a?utm_source=x", title="A")
    b = bm.add("https://EXAMPLE.org/a", title="A again")
    assert a.id == b.id and len(vault.list("sources")) == 1


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x.org/f", "https://u:p@x.org/",
                                 "not a url", "http://"])
def test_only_plain_web_addresses_can_be_bookmarked(vault, url):
    with pytest.raises(BookmarkError):
        Bookmarks(vault).add(url)


def test_normalise_drops_fragments_and_tracking_but_keeps_real_queries():
    assert normalise("https://Example.org/p?id=7&utm_campaign=z&fbclid=1#frag") == "https://example.org/p?id=7"


# ---- 609 glossary ----------------------------------------------------------------------------------------
def test_glossary_terms_feed_speech_hints_within_a_cap(tmp_path):
    g = Glossary(tmp_path / "glossary.md")
    g.add("SPS", "Sacred Palm School")
    g.add("Tele-MANAS", "the national helpline")
    assert g.stt_hint() == "SPS, Tele-MANAS"
    for i in range(300):
        g.add(f"term{i}", "x")
    assert len(g.stt_hint()) <= 400


def test_the_prompt_only_carries_terms_the_request_uses(tmp_path):
    g = Glossary(tmp_path / "glossary.md")
    g.add("SPS", "Sacred Palm School")
    g.add("PB", "personal best")
    assert g.prompt_line("when is SPS closed today") == "Your words: SPS = Sacred Palm School."
    assert g.prompt_line("how is the weather") == ""


def test_glossary_survives_a_restart_and_a_term_can_be_replaced(tmp_path):
    p = tmp_path / "glossary.md"
    Glossary(p).add("SPS", "school")
    Glossary(p).add("sps", "Sacred Palm School")  # same term, any case: replaced
    again = Glossary(p)
    assert again.entries() == {"sps": "Sacred Palm School"}


def test_a_glossary_file_edited_by_hand_with_junk_lines_still_loads(tmp_path):
    p = tmp_path / "glossary.md"
    p.write_text("# my words\n\nSPS = school\nthis line has no equals\n= nothing\nPB = personal best\n")
    assert Glossary(p).entries() == {"SPS": "school", "PB": "personal best"}
