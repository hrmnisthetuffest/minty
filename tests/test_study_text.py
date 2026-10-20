"""581 summariser, 585 voice note structuring, 730 rehearsal feedback, 587 notice triage."""
from datetime import date

import pytest

from minty_core.inbox import Notice
from minty_core.study.text import rehearse, structure_note, summarise, triage_notices

CHAPTER = """Photosynthesis

Photosynthesis is the process by which green plants make their own food. Plants take in carbon dioxide from the air
and water from the soil. Sunlight provides the energy for the reaction. Chlorophyll in the leaves absorbs sunlight.

The Light Reaction

In the light reaction, chlorophyll absorbs light energy and splits water into hydrogen and oxygen. Oxygen is released
into the air through the stomata. The hydrogen is used in the next stage. The light reaction happens in the grana of
the chloroplast.

The Dark Reaction

In the dark reaction, carbon dioxide is combined with hydrogen to make glucose. This stage does not need light
directly. The dark reaction happens in the stroma of the chloroplast. Glucose is stored as starch in the leaves.
"""


def test_the_summary_is_extractive_short_and_grouped_by_heading():
    s = summarise(CHAPTER, points=6)
    flat = " ".join(CHAPTER.split())
    for point in s.points:
        assert " ".join(point.split()) in flat  # every point is a sentence from the text
    assert 3 <= len(s.points) <= 6
    assert "Photosynthesis" in s.title
    assert any("Light Reaction" in h for h, _ in s.sections) and any("Dark Reaction" in h for h, _ in s.sections)
    assert {"reaction", "photosynthesis"} & {t.lower() for t in s.terms}
    assert all(t.lower() in CHAPTER.lower() for t in s.terms)  # terms come from the text itself
    text = s.text()
    assert text.startswith("Photosynthesis") and "- " in text


def test_nothing_to_summarise_is_said_plainly_and_huge_text_is_capped():
    assert summarise("   ").text() == "There is no text to summarise."
    big = ("Water boils at one hundred degrees. " * 5000)
    assert len(summarise(big).text()) < 3000


def test_structure_a_dictated_note():
    said = ("um so today in physics we learned about refraction. Light bends when it goes from air into water. "
            "The refractive index measures how much it bends. What is Snell's law? I need to revise Snell's law "
            "before friday. Remember to bring the glass slab. Refractive Index is the ratio of the speeds.")
    n = structure_note(said, subject="Physics")
    assert n.title.startswith("Today in physics we learned about refraction")
    assert "Light bends when it goes from air into water" in n.key_points
    assert n.questions == ["What is Snell's law?"]
    assert any("revise Snell's law before friday" in t for t in n.todos)
    assert any("bring the glass slab" in t for t in n.todos)
    md = n.markdown()
    assert md.startswith("# ") and "## Key points" in md and "## Questions" in md and "## To do" in md
    assert "um so" not in md.lower()


def test_empty_dictation_is_refused():
    with pytest.raises(ValueError):
        structure_note("   ")


def test_rehearsal_reports_pace_fillers_and_long_sentences():
    talk = ("So um today I want to talk about the water cycle. You know, the water cycle is basically how water "
            "moves around the earth, and um it goes up as vapour and then it comes down as rain and then it flows "
            "into rivers and then it goes back to the sea and then it starts all over again which is basically "
            "the whole point. Like, thank you.")
    r = rehearse(talk, seconds=20)
    assert r.pace == "fast" and r.wpm > 170
    assert r.fillers["um"] == 2 and r.fillers["basically"] == 2 and r.fillers["you know"] == 1
    assert r.longest_sentence_words >= 30
    text = r.text()
    assert "fast" in text and "um" in text and "shorter" in text.lower()


def test_rehearsal_without_a_time_still_counts_fillers():
    r = rehearse("Um, hello everyone. Welcome.", seconds=None)
    assert r.wpm is None and r.pace == "unknown" and r.fillers["um"] == 1
    assert "no timing" in r.text().lower()


def N(title, body="", day=None):
    return Notice(1, title, body, day, "portal", False)


def test_notices_are_sorted_by_what_needs_you():
    today = date(2026, 10, 15)
    items = [N("Annual Day rehearsal schedule", "Practice from 3 pm."),
             N("Term fee due", "Fee for term 2 must be paid by 18 Oct.", date(2026, 10, 18)),
             N("Unit test in Maths", "Chapters 4 and 5 on 20 Oct.", date(2026, 10, 20)),
             N("Diwali holiday", "School closed 28 Oct to 2 Nov."),
             N("Consent form for trip", "Sign and return the slip by tomorrow."),
             N("Library has new books", "")]
    out = triage_notices(items, today)
    assert [t.category for t in out] == ["form", "fee", "exam", "holiday", "event", "other"]
    assert out[0].priority == "high" and out[1].priority == "high" and out[5].priority == "low"
    assert "pay" in out[1].action.lower() and "18 Oct" in out[1].action
    assert "sign" in out[0].action.lower()
    assert "revise" in out[2].action.lower()
