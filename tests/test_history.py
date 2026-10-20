import json
from datetime import datetime, timedelta

import pytest

from minty_core.history import ConversationArchive, extractive_summary

DAY = 86400.0


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


def noon(y, m, d):
    return datetime(y, m, d, 12, 0).astimezone().timestamp()


async def fake_summary(instruction, text, max_chars):
    return f"SUMMARY[{len(text.splitlines())} lines]"


def test_log_writes_private_jsonl_and_raw_day_reads_it_back(tmp_path):
    clock = Clock(noon(2026, 10, 10))
    a = ConversationArchive(tmp_path, clock)
    a.log("user", "what is the capital of France")
    a.log("assistant", "Paris.")
    a.log("user", "   ")  # blank lines are ignored
    path = tmp_path / "raw" / "2026-10-10.jsonl"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert a.raw_day("2026-10-10") == "You: what is the capital of France\nMinty: Paris."
    assert a.raw_day("../etc/passwd") == "" and a.raw_days() == ["2026-10-10"]


def test_earlier_today_is_word_for_word_but_excludes_the_live_window(tmp_path):
    clock = Clock(noon(2026, 10, 10))
    a = ConversationArchive(tmp_path, clock)
    a.log("user", "old question")
    clock.now += 45 * 60
    a.log("user", "recent question")
    clock.now += 5 * 60
    block = a.context_block(window_min=30)
    assert "old question" in block and "recent question" not in block


async def test_a_finished_day_is_summarised_only_after_midnight(tmp_path):
    clock = Clock(noon(2026, 10, 10))
    a = ConversationArchive(tmp_path, clock)
    a.log("user", "remember I like jazz")
    assert await a.compact(fake_summary) == []  # same day: nothing yet
    clock.now += DAY
    assert await a.compact(fake_summary) == ["days/2026-10-10"]
    assert await a.compact(fake_summary) == []  # idempotent
    assert "SUMMARY" in a.context_block()
    assert "remember I like jazz" in a.raw_day("2026-10-10")  # raw copy untouched


async def test_a_finished_month_is_rolled_up_except_its_last_day(tmp_path):
    clock = Clock(noon(2026, 9, 1))
    a = ConversationArchive(tmp_path, clock)
    for day in (1, 2, 30):
        clock.now = noon(2026, 9, day)
        a.log("user", f"talk on the {day}th")
    clock.now = noon(2026, 10, 2)
    written = await a.compact(fake_summary)
    assert "months/2026-09" in written
    assert (tmp_path / "months" / "2026-09.md").exists()
    assert (tmp_path / "days" / "2026-09-30.md").exists()  # last day kept on its own
    assert not (tmp_path / "days" / "2026-09-01.md").exists()
    assert a.raw_day("2026-09-01") == "You: talk on the 1th"  # full transcript still there
    assert await a.compact(fake_summary) == []


def test_search_and_listing_are_raw_only(tmp_path):
    clock = Clock(noon(2026, 10, 10))
    a = ConversationArchive(tmp_path, clock)
    a.log("user", "My dentist appointment is on Friday")
    assert a.search_raw("dentist") == ["2026-10-10 You: My dentist appointment is on Friday"]
    assert a.search_raw("") == [] and a.search_raw("nothing here") == []


def test_extractive_fallback_keeps_user_lines_within_budget():
    text = "\n".join(f"You: line {i} " + "x" * 50 for i in range(100))
    out = extractive_summary("", text, 300)
    assert out.startswith("- line 0") and len(out) <= 300


async def test_each_finished_day_triggers_a_reflection(tmp_path):
    clock = Clock(noon(2026, 10, 10))
    a = ConversationArchive(tmp_path, clock)
    a.log("user", "I hate long answers")
    seen = []

    async def reflect(day, text):
        seen.append((day, text))

    clock.now += DAY
    await a.compact(fake_summary, reflect)
    await a.compact(fake_summary, reflect)  # nothing new: no second reflection
    assert seen == [("2026-10-10", "You: I hate long answers")]


async def test_a_failing_reflection_does_not_block_compaction(tmp_path):
    clock = Clock(noon(2026, 10, 10))
    a = ConversationArchive(tmp_path, clock)
    a.log("user", "hello")

    async def boom(day, text):
        raise RuntimeError("no model")

    clock.now += DAY
    assert await a.compact(fake_summary, boom) == ["days/2026-10-10"]


def test_restart_moves_the_conversation_to_earlier_today_immediately(tmp_path):
    clock = Clock(noon(2026, 10, 10))
    a = ConversationArchive(tmp_path, clock)
    clock.now += 60
    a.log("user", "plan my trip to Rome")
    a.log("assistant", "Sure, when do you go?")
    assert a.context_block(window_min=30) == ""  # still inside the live window
    clock.now += 5
    a.mark_reset()
    block = a.context_block(window_min=30)
    assert "plan my trip to Rome" in block and "when do you go" in block
    clock.now += 5
    a.log("user", "what is the weather")  # after the reset: live, not 'earlier'
    assert "what is the weather" not in a.context_block(window_min=30)


def test_a_process_restart_does_not_lose_the_recent_conversation(tmp_path):
    clock = Clock(noon(2026, 10, 10))
    first = ConversationArchive(tmp_path, clock)
    first.log("user", "remember the dentist is at 3")
    clock.now += 120  # core restarted two minutes later
    second = ConversationArchive(tmp_path, clock)
    assert "dentist is at 3" in second.context_block(window_min=30)
