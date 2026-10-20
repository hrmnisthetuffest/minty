"""The study tools as the model sees them."""
from datetime import date, timedelta

import pytest

from minty_core.inbox import Inbox
from tests.plugin_helpers import make_host


def host(tmp_path, enabled=True, **study):
    h = make_host(tmp_path / "cfg")
    data = h._store.config.model_dump(mode="json")
    data["study"].update({"enabled": enabled, **study})
    h._store.update(data)
    return h


async def test_off_until_switched_on_and_nothing_is_created(tmp_path):
    h = host(tmp_path, enabled=False)
    assert "switched off" in await h.call("study_cards", {"action": "stats"})
    assert not (tmp_path / "cfg" / "study.db").exists()


async def test_the_tutor_check_flags_a_wrong_result_and_scans_text(tmp_path):
    h = host(tmp_path)
    assert (await h.call("study_check", {"statement": "7 * 8", "claimed": "56"})).startswith("ok")
    out = await h.call("study_check", {"statement": "7 * 8", "claimed": "54"})
    assert out.startswith("wrong") and "56" in out
    assert "56 + 6 = 63 is wrong" in await h.call("study_check", {"statement": "We get 56 + 6 = 63 here."})
    assert (await h.call("study_check", {"statement": "the area", "claimed": "12"})).startswith("unchecked")
    for name in ("study_check", "study_summarise", "study_rehearse"):
        assert h.get_tool(name).risk == 0


async def test_flashcards_through_the_tools_with_a_spoken_answer(tmp_path):
    h = host(tmp_path)
    assert (await h.call("study_cards", {"action": "add", "deck": "maths", "front": "7 x 8", "back": "56"})
            ).startswith("Card")
    assert "Question: 7 x 8" in await h.call("study_cards", {"action": "next"})
    assert await h.call("study_cards", {"action": "answer", "said": "fifty six"}) == "Right."
    assert "Nothing is due" in await h.call("study_cards", {"action": "next"})
    await h.call("study_cards", {"action": "add", "deck": "maths", "front": "9 x 9", "back": "81"})
    await h.call("study_cards", {"action": "next"})
    miss = await h.call("study_cards", {"action": "answer", "said": "72"})
    assert "The answer is: 81" in miss
    assert (await h.call("study_cards", {"action": "grade", "how": "hard"})).startswith("It will come back")
    assert await h.call("study_cards", {"action": "stats"}) == "2 cards: 0 due now, 0 not started."
    assert h.get_tool("study_cards").risk({"action": "next"}) == 0 and h.get_tool("study_cards").risk(
        {"action": "add"}) == 1


async def test_exams_and_the_homework_plan_use_what_is_saved(tmp_path):
    h = host(tmp_path)
    soon = date.today() + timedelta(days=9)
    assert "Added" in await h.call("study_exam", {"action": "add", "subject": "maths", "date": soon.isoformat(),
                                                  "topics": "algebra, geometry"})
    assert "Maths in 9 days" in await h.call("study_exam", {"action": "list"})
    plan = await h.call("study_exam", {"action": "plan", "subject": "maths"})
    assert "algebra" in plan and "quick review of everything" in plan
    inbox = Inbox(tmp_path / "cfg" / "inbox.db")
    inbox.add_homework("Physics", "Lab report", date.today() + timedelta(days=4))
    inbox.add_homework("English", "Poem", None)
    inbox.close()
    out = await h.call("study_plan", {})
    assert "Physics: Lab report" in out and "No due date" in out and "English" in out


async def test_summarise_note_and_rehearse(tmp_path):
    h = host(tmp_path)
    chapter = ("Light travels in straight lines. Reflection is the bouncing of light from a surface. "
               "A mirror reflects almost all the light that falls on it. The angle of incidence equals the angle of "
               "reflection. Refraction is the bending of light when it enters another medium.")
    assert "Reflection" in await h.call("study_summarise", {"text": chapter, "points": 3})
    out = await h.call("study_note", {"text": "um today we did reflection. What is refraction? Remember to bring "
                                              "the mirror.", "subject": "Physics"})
    assert out.startswith("# ") and "## Questions" in out and "## To do" in out
    assert "words a minute" in await h.call("study_rehearse", {"text": "hello there everyone welcome", "seconds": 10})


async def test_papers_and_mistakes_and_the_weekly_review(tmp_path):
    h = host(tmp_path)
    paper = "1. State Newton's first law. [2 marks]\n2. Define refraction. [2 marks]\n"
    assert await h.call("study_paper", {"action": "import", "subject": "physics", "text": paper}) == "Added 2 questions."
    assert "Newton" in await h.call("study_paper", {"action": "practice", "subject": "physics", "minutes": 10})
    assert (await h.call("study_mistake", {"action": "log", "subject": "maths", "topic": "fractions",
                                           "what": "added tops and bottoms", "fix": "common denominator"})
            ).startswith("Logged")
    assert "fractions" in await h.call("study_mistake", {"action": "list"})
    assert "added tops and bottoms" in await h.call("study_mistake", {"action": "drill"})
    review = await h.call("study_review", {})
    assert review.startswith("This week:") and "1 mistake" in review


async def test_the_class_bag_comes_from_the_timetable(tmp_path):
    from datetime import datetime

    tomorrow = (datetime.now() + timedelta(days=1))
    key = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[tomorrow.weekday()]
    h = host(tmp_path, timetable={key: [{"start": "08:00", "end": "08:40", "subject": "Maths",
                                         "bring": "geometry box"}]})
    assert "Maths (geometry box)" in await h.call("study_class_bag", {})
    assert "No timetable" in await host(tmp_path / "x").call("study_class_bag", {})


# ---- loops and Telegram -----------------------------------------------------------------------------------
def test_class_reminders_the_bag_and_the_review_are_each_said_once(tmp_path):
    from datetime import datetime

    from minty_core.config import Config
    from minty_core.study.kit import StudyProvider, due_notices

    cfg = Config()
    cfg.study.enabled, cfg.study.class_reminders, cfg.study.weekly_review = True, True, True
    cfg.study.timetable = {"mon": [{"start": "08:00", "end": "08:40", "subject": "Maths", "bring": "geometry box"}],
                           "tue": [{"start": "08:00", "end": "08:40", "subject": "English", "bring": ""}]}
    p = StudyProvider(config=lambda: cfg, directory=tmp_path)
    state: dict = {}
    mon_early = datetime(2026, 10, 12, 7, 50)
    out, state = due_notices(p, mon_early, state, lambda: "review text")
    assert out == [("Maths at 08:00. Bring: geometry box.", True)]
    out, state = due_notices(p, mon_early, state, lambda: "review text")
    assert out == []  # said once
    evening = datetime(2026, 10, 12, 19, 40)
    out, state = due_notices(p, evening, state, lambda: "review text")
    assert out == [("Tomorrow: English. Nothing special to pack.", False)]
    out, state = due_notices(p, evening, state, lambda: "review text")
    assert out == []
    sunday = datetime(2026, 10, 18, 18, 30)
    out, state = due_notices(p, sunday, state, lambda: "review text")
    assert ("review text", False) in out
    out, state = due_notices(p, sunday, state, lambda: "review text")
    assert out == []
    cfg.study.enabled = False
    assert due_notices(p, mon_early, {}, lambda: "x") == ([], {})


async def test_a_captioned_pdf_or_photo_is_summarised_locally_with_no_model(tmp_path):
    from minty_core.config import Config
    from minty_core.study.kit import StudyProvider, summarise_upload

    cfg = Config()
    cfg.study.enabled = True
    p = StudyProvider(config=lambda: cfg, directory=tmp_path)
    chapter = ("Light travels in straight lines. Reflection is the bouncing of light from a surface. "
               "A mirror reflects almost all the light that falls on it. Refraction is the bending of light.")
    said = summarise_upload(p, "pdf", chapter, "summarise this")
    assert said and "Reflection" in said
    assert summarise_upload(p, "pdf", chapter, "what is this") is None
    assert summarise_upload(p, "other", chapter, "summarise") is None
    cfg.study.enabled = False
    assert summarise_upload(p, "pdf", chapter, "summarise this") is None
