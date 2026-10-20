"""WP-14 acceptance (docs/plan-master.md), through the real application wiring."""
import re
from datetime import date, datetime, time, timedelta

import pytest

from minty_core.context import build_context
from minty_core.inbox import Inbox
from tests.test_study_verify import FIXTURES

DAY = 86400.0


@pytest.fixture
def ctx(tmp_path):
    c = build_context(tmp_path / "cfg")
    data = c.store.config.model_dump(mode="json")
    data["study"]["enabled"] = True
    data["study"]["busy"] = {"tue": ["17:00-18:00"], "thu": ["16:30-17:30"]}
    c.store.update(data)
    return c


async def test_a_week_of_homework_gives_a_schedule_with_no_overlap_with_classes_or_quiet_hours(ctx, tmp_path):
    today = date.today()
    inbox = Inbox(tmp_path / "cfg" / "inbox.db")
    for subject, task, days in [("Maths", "Exercise 4.2", 2), ("Physics", "Lab report", 5), ("English", "Essay", 4),
                                ("Chemistry", "Worksheet", 1), ("History", "Chapter 7 notes", 6),
                                ("Hindi", "Poem", 3), ("CS", "Program", 7)]:
        inbox.add_homework(subject, task, today + timedelta(days=days))
    inbox.close()
    out = await ctx.host.call("study_plan", {})
    planner = ctx.services["study"].planner()
    cfg = ctx.store.config
    night_from, night_to = cfg.proactive.night_from, cfg.proactive.night_to
    seen = 0
    per_day: dict[str, list[tuple[int, int]]] = {}
    for line in out.splitlines():
        m = re.match(r"(\w+ \d{2} \w{3}): (.*)", line)
        if not m:
            continue
        for a_h, a_m, b_h, b_m in re.findall(r"(\d{2}):(\d{2})-(\d{2}):(\d{2})", m.group(2)):
            start, end = int(a_h) * 60 + int(a_m), int(b_h) * 60 + int(b_m)
            seen += 1
            day = datetime.strptime(f"{m.group(1)} {today.year}", "%A %d %b %Y").date()
            if planner.is_weekday(day):  # nothing before the study window opens
                assert start >= 16 * 60 + 30 and end <= 20 * 60 + 30
            h = lambda t: int(t[:2]) * 60 + int(t[3:])  # noqa: E731
            assert not (start >= h(night_from) or end <= h(night_to) or end > h(night_from))  # not in the night
            for a, b in per_day.setdefault(m.group(1), []):
                assert end <= a or start >= b  # no two blocks overlap
            per_day[m.group(1)].append((start, end))
    assert seen >= 6  # seven days; once today's study window has passed (late evening) only six remain


async def test_thirty_algebra_and_arithmetic_fixtures_are_verified_or_flagged_before_they_are_said(ctx):
    assert len(FIXTURES) == 30
    for expr, claimed, right in FIXTURES:
        out = await ctx.host.call("study_check", {"statement": expr, "claimed": claimed})
        assert out.startswith("ok" if right else "wrong"), (expr, claimed, out)


async def test_flashcard_state_survives_a_restart_and_due_cards_are_deterministic(ctx, tmp_path):
    await ctx.host.call("study_cards", {"action": "add", "deck": "maths", "front": "7 x 8", "back": "56"})
    await ctx.host.call("study_cards", {"action": "next"})
    assert await ctx.host.call("study_cards", {"action": "answer", "said": "56"}) == "Right."
    kit = ctx.services["study"].kit()
    card = kit.cards.get(1)
    due = card["due"]
    assert card["reps"] == 1 and due > card["last_review"]
    # a brand-new provider on the same folder (a restart) sees the same state and the same due list
    from minty_core.study.kit import StudyProvider

    again = StudyProvider(config=lambda: ctx.store.config, directory=tmp_path / "cfg").kit()
    same = again.cards.get(1)
    assert (same["due"], same["stability"], same["difficulty"], same["reps"]) == (
        due, card["stability"], card["difficulty"], 1)
    assert [c["id"] for c in again.cards.due(due)] == [1] and again.cards.due(due - 1) == []


async def test_the_tutor_does_not_write_graded_answers_for_you(ctx):
    from minty_core.skills import SkillIndex, BUILTIN_SKILLS_DIR

    body = SkillIndex([BUILTIN_SKILLS_DIR]).skills()["verified-tutor"].body
    assert "do not give the final answer" in body.lower() and "essays" in body.lower()
    assert "study_check" in body
