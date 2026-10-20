"""The productivity tools as the model sees them, through the real plugin host."""
import asyncio
import wave
from datetime import date, datetime, timedelta

import pytest

from tests.plugin_helpers import make_host


def host(tmp_path, **cfg):
    h = make_host(tmp_path / "cfg", services={"publish": lambda kind, **f: h_events.append((kind, f))})
    data = h._store.config.model_dump(mode="json")
    data["productivity"]["enabled"] = cfg.pop("enabled", True)
    data["recorder"].update(cfg.pop("recorder", {}))
    data["butler"]["enabled"] = cfg.pop("butler", False)
    for k, v in cfg.items():
        data[k].update(v)
    h._store.update(data)
    return h


h_events: list = []


@pytest.fixture(autouse=True)
def _clear():
    h_events.clear()


async def test_off_until_switched_on(tmp_path):
    h = host(tmp_path, enabled=False)
    assert "switched off" in await h.call("productivity_habit", {"action": "status"})
    assert not (tmp_path / "cfg" / "life.db").exists()


async def test_habits_and_streaks(tmp_path):
    h = host(tmp_path)
    await h.call("productivity_habit", {"action": "add", "name": "Read 20 pages"})
    out = await h.call("productivity_habit", {"action": "check", "name": "read"})
    assert out.startswith("Ticked Read 20 pages. Streak: 1 day")
    assert "done today" in await h.call("productivity_habit", {"action": "status"})
    assert "don't have a habit" in await h.call("productivity_habit", {"action": "check", "name": "swimming"})


async def test_people_with_consent_and_a_real_delete(tmp_path):
    h = host(tmp_path, butler=True)
    await h.call("productivity_person", {"action": "add", "name": "Sam Taylor", "birthday": "03-21"})
    await h.call("productivity_person", {"action": "fact", "name": "sam", "text": "Likes mango lassi",
                                         "source": "told me"})
    assert "mango lassi (told me)" in await h.call("productivity_person", {"action": "show", "name": "sam"})
    assert "ID numbers" in await h.call("productivity_person", {"action": "fact", "name": "sam",
                                                                "text": "aadhaar 2345 6789 0124", "source": "told me"})
    await h.call("butler_add", {"domain": "waiting", "name": "Sam: the notes", "when": "friday"})
    assert h.get_tool("productivity_person").risk({"action": "delete"}) == 3
    out = await h.call("productivity_person", {"action": "delete", "name": "sam"})
    assert out.startswith("Deleted them")
    assert "I don't know" in await h.call("productivity_person", {"action": "show", "name": "sam"})
    assert "empty" in await h.call("butler_list", {"domain": "waiting"})  # the derived follow-up went too


async def test_trip_meals_and_alarms(tmp_path):
    h = host(tmp_path)
    assert "packing list for Manali" in await h.call("productivity_trip", {"action": "create", "name": "Manali",
                                                                           "kind": "hills", "days": 4})
    assert "Warm jacket" in await h.call("productivity_trip", {"action": "remaining", "name": "manali"})
    assert (await h.call("productivity_trip", {"action": "check", "name": "manali", "item": "warm jacket"})
            ).startswith("Packed")
    await h.call("productivity_meal", {"action": "plan", "name": "Dal chawal", "date": date.today().isoformat()})
    assert "Added 5 items" in await h.call("productivity_meal", {"action": "to_shopping",
                                                                 "date": date.today().isoformat()})
    assert "dal" in await h.call("productivity_meal", {"action": "shopping"})
    assert "Alarm set for 06:30" in await h.call("productivity_alarm", {"action": "add", "time": "06:30",
                                                                        "days": "weekdays"})
    assert "06:30 mon,tue,wed,thu,fri" in await h.call("productivity_alarm", {"action": "list"})
    assert "Nothing is ringing" in await h.call("productivity_alarm", {"action": "stop"})


async def test_todo_triage_and_the_day_text(tmp_path):
    h = host(tmp_path, butler=True)
    await h.call("butler_add", {"domain": "tasks", "name": "submit the science form"})
    out = await h.call("productivity_todo", {"action": "triage"})
    assert "submit the science form" in out and "nothing changes until you do" in out
    import re

    ident = int(re.search(r"\[(\d+)\]", out).group(1))
    assert (await h.call("productivity_todo", {"action": "apply", "ids": [ident]})).startswith("Gave 1 task")
    # the deadline-feel task was dated two days out, so today's plan does not carry it yet
    assert "science form" not in await h.call("productivity_day", {"kind": "plan"})
    await h.call("butler_add", {"domain": "tasks", "name": "call the plumber", "when": "today at 6 pm"})
    plan = await h.call("productivity_day", {"kind": "plan"})
    assert "To do: call the plumber" in plan
    await h.call("productivity_habit", {"action": "add", "name": "Water"})
    assert "Habits: Water" in await h.call("productivity_day", {"kind": "plan"})
    summary = await h.call("productivity_day", {"kind": "summary"})
    assert "Today" in summary
    assert "Time to wind down." in await h.call("productivity_day", {"kind": "wind_down"})


async def test_the_recorder_has_its_own_switch_and_makes_a_note_with_the_consent_reminder(tmp_path):
    h = host(tmp_path)
    assert "switched off" in await h.call("productivity_record", {"action": "start"})
    h = host(tmp_path, recorder={"enabled": True})

    async def transcribe(pcm, rate):
        return "Today we studied refraction. Remember to revise Snell's law before friday."

    h._services["transcribe"] = transcribe
    start = await h.call("productivity_record", {"action": "start", "label": "Physics"})
    assert "agrees" in start.lower() and ("recording", {"active": True, "label": "Physics"}) in h_events
    rec = h._services["productivity"].recorder()
    rec.feed(b"\x01\x00" * 16000)
    assert "Marked" in await h.call("productivity_record", {"action": "mark", "label": "formula"})
    assert "Recording Physics" in await h.call("productivity_record", {"action": "status"})
    out = await h.call("productivity_record", {"action": "stop"})
    assert "making the notes" in out
    for _ in range(100):
        await asyncio.sleep(0.02)
        if any(k == "notify" for k, _ in h_events):
            break
    assert any(k == "notify" and "ready" in f["text"] for k, f in h_events)
    notes = list((tmp_path / "cfg" / "recording-notes").glob("*.md"))
    assert len(notes) == 1 and "## Consent" in notes[0].read_text() and "revise Snell's law" in notes[0].read_text()
    assert not list((tmp_path / "cfg" / "recordings").glob("*/*.wav"))  # the audio is gone
