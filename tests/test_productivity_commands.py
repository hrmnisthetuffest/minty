"""The voice phrases for alarms, habits, the day and the recorder (whole-utterance matches only)."""
from datetime import datetime

import pytest

from minty_core.config import Config
from minty_core.productivity.commands import ProductivityCommands
from minty_core.productivity.kit import ProductivityProvider


def make(tmp_path, productivity=True, recorder=False):
    cfg = Config()
    cfg.productivity.enabled = productivity
    cfg.recorder.enabled = recorder
    events = []
    provider = ProductivityProvider(lambda: cfg, tmp_path, {})
    return ProductivityCommands(provider, lambda kind, **f: events.append((kind, f))), provider, events


def test_nothing_is_handled_while_the_switch_is_off(tmp_path):
    cmds, _p, _e = make(tmp_path, productivity=False)
    for text in ("set an alarm for 6:30", "my plan for today", "how was my day", "i did reading", "wind down"):
        assert cmds.handle(text) is None
    assert not (tmp_path / "life.db").exists()


def test_set_an_alarm_by_voice(tmp_path):
    cmds, p, _e = make(tmp_path)
    assert cmds.handle("set an alarm for 6:30 on weekdays") == "Alarm set for 06:30, weekdays."
    assert cmds.handle("set an alarm for 6 pm") == "Alarm set for 18:00."
    assert cmds.handle("wake me up at 7 am") == "Alarm set for 07:00."
    assert [(a["at"], a["days"]) for a in p.life().alarms.list()] == [
        ("06:30", "mon,tue,wed,thu,fri"), ("07:00", ""), ("18:00", "")]


def test_a_time_that_is_not_a_time_is_left_to_the_model(tmp_path):
    cmds, _p, _e = make(tmp_path)
    assert cmds.handle("set an alarm for 25:70") is None
    assert cmds.handle("set an alarm for when the bell rings") is None


def test_snooze_and_stop_only_act_on_a_ringing_alarm(tmp_path):
    cmds, p, _e = make(tmp_path)
    assert cmds.handle("snooze") is None  # nothing is ringing: not ours
    assert cmds.handle("stop the alarm") == "Nothing is ringing."
    ident = p.life().alarms.add("06:30")
    p.life().alarms.start_ring(ident)
    assert cmds.handle("snooze the alarm") == "Snoozed for 8 minutes."
    p.life().alarms.start_ring(ident)
    assert cmds.handle("stop the alarm") == "Stopped."
    assert p.life().alarms.ringing == []


def test_i_did_a_habit_ticks_it_and_other_sentences_are_left_alone(tmp_path):
    cmds, p, _e = make(tmp_path)
    p.life().habits.add("Reading")
    assert cmds.handle("i did my reading").startswith("Ticked off Reading")
    assert cmds.handle("i did my homework") is None  # not a habit: normal conversation
    assert cmds.handle("i did reading") .startswith("Ticked off Reading")


def test_the_day_phrases(tmp_path):
    cmds, _p, _e = make(tmp_path)
    assert "Nothing is planned" in cmds.handle("my plan for today")
    assert "Today" in cmds.handle("how was my day")
    assert "Time to wind down." in cmds.handle("time to wind down")


def test_recording_phrases_need_the_recorder_switch(tmp_path):
    cmds, _p, _e = make(tmp_path, recorder=False)
    assert "switched off" in cmds.handle("start recording")
    cmds, p, events = make(tmp_path, recorder=True)
    assert cmds.handle("mark this moment") == "I'm not recording."
    reply = cmds.handle("start recording the maths lecture")
    assert "recording" in reply.lower() and "everyone" in reply.lower()  # the consent reminder
    assert ("recording", {"active": True, "label": "the maths lecture"}) in events
    assert cmds.handle("mark this moment").startswith("Marked")
    assert p.recorder().recording
