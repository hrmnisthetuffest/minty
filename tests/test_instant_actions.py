from datetime import datetime

import pytest

from minty_core.instant.actions import ACTIONS, _timers_said, match
from tests.instant_fakes import IST, NOW

WAKE = ("minty",)


def act(text, now=NOW):
    return match(text, WAKE, now)


@pytest.mark.parametrize("said, seconds, label", [
    ("set a timer for 5 minutes", 300, ""), ("Minty, timer for 90 seconds", 90, ""), ("start a 10 minute timer", 600, ""),
    ("set a pasta timer for twelve minutes", 720, "pasta"), ("set a timer for an hour and a half", 5400, ""),
    ("set a timer for half an hour", 1800, ""), ("set an alarm in 20 minutes", 1200, ""), ("5 minute timer", 300, ""),
    ("set a timer for 2 hours 15 minutes", 8100, ""), ("make a 45 second timer", 45, ""),
])
def test_timers(said, seconds, label):
    a = act(said)
    assert a.tool == "timers_set_timer" and a.args == {"seconds": seconds, "label": label}
    assert a.say.startswith("Timer set for ")


@pytest.mark.parametrize("said", ["set a timer", "set a timer for soon", "set a timer for 100 hours", "timer for banana", "set a timer for 0 minutes"])
def test_unclear_timers_go_to_the_model(said):
    assert act(said) is None


def test_the_timer_reply_says_the_length():
    assert act("set a timer for 90 seconds").say == "Timer set for 1 minute 30 seconds."


def test_asking_about_timers_reads_the_tool_result_aloud():
    a = act("how much time is left on the timer")
    assert a.tool == "timers_list_timers" and callable(a.say)
    assert a.say("Timer 1 (timer): 125 seconds left.") == "2 minutes 5 seconds left on the timer."
    assert _timers_said("Timer 1 (pasta): 600 seconds left. Timer 2 (timer): 30 seconds left.") == (
        "10 minutes left on the pasta timer, and 30 seconds left on the timer.")
    assert _timers_said("No timers running.") == "No timers running."


@pytest.mark.parametrize("said, text, minutes", [
    ("remind me in 10 minutes to call mom", "call mom", 10), ("remind me to check the oven in 20 minutes", "check the oven", 20),
    ("remind me in an hour to drink water", "drink water", 60), ("remind me to turn in the report in 2 hours", "turn in the report", 120),
])
def test_reminders_after_a_wait(said, text, minutes):
    a = act(said)
    assert a.tool == "reminders_set" and a.args == {"text": text, "in_minutes": minutes}


@pytest.mark.parametrize("said, text, at, spoken", [
    ("remind me at 6 pm to take medicine", "take medicine", "2026-10-20T18:00", "6 PM"),
    ("remind me to stretch at 4:30 pm", "stretch", "2026-10-21T16:30", "4:30 PM"),
    ("remind me at 6 to eat", "eat", "2026-10-20T18:00", "6 PM"),
    ("remind me to call at 9", "call", "2026-10-20T21:00", "9 PM"),
    ("remind me to eat at 12", "eat", "2026-10-21T12:00", "noon"),
    ("remind me to leave at 17:45", "leave", "2026-10-20T17:45", "5:45 PM"),
])
def test_reminders_at_a_time(said, text, at, spoken):
    a = act(said)
    assert a.tool == "reminders_set" and a.args == {"text": text, "at": at} and a.say == f"Okay, I'll remind you at {spoken}."


@pytest.mark.parametrize("said", ["remind me to call mom", "remind me tomorrow at 9 to call mom", "remind me", "remind me at 25 to x",
                                  "remind me in a while to x"])
def test_reminders_that_need_more_are_left_to_the_model(said):
    assert act(said) is None


@pytest.mark.parametrize("said, args", [
    ("volume up", {"action": "volume", "value": "up"}), ("louder", {"action": "volume", "value": "up"}),
    ("turn the volume down", {"action": "volume", "value": "down"}), ("quieter", {"action": "volume", "value": "down"}),
    ("set the volume to 40", {"action": "volume", "value": "40"}), ("volume 60 percent", {"action": "volume", "value": "60"}),
    ("set the volume to forty", {"action": "volume", "value": "40"}), ("volume to max", {"action": "volume", "value": "100"}),
    ("mute the sound", {"action": "mute"}), ("unmute the speakers", {"action": "unmute"}),
    ("set the brightness to 50", {"action": "brightness", "value": "50"}), ("lock the screen", {"action": "lock"}),
])
def test_system_controls(said, args):
    a = act(said)
    assert a.tool == "system_control" and a.args == args


@pytest.mark.parametrize("said", ["set the volume to 400", "set the brightness to 2", "mute", "turn it all up to eleven"])
def test_out_of_range_or_ambiguous_controls_are_left_alone(said):
    assert act(said) is None


@pytest.mark.parametrize("said, tool", [
    ("pause", "spotify_pause"), ("pause the music", "spotify_pause"), ("resume the music", "spotify_resume"), ("unpause", "spotify_resume"),
    ("next song", "spotify_next"), ("skip this song", "spotify_next"), ("skip", "spotify_next"),
    ("previous song", "spotify_previous"), ("go back a song", "spotify_previous"),
])
def test_music_controls(said, tool):
    assert act(said).tool == tool


@pytest.mark.parametrize("said", ["play some music", "play despacito", "next", "back", "stop", "play the next episode of the show",
                                  "what is the weather", "pause the meeting and call mom"])
def test_things_that_are_not_controls_are_left_alone(said):
    assert act(said) is None


def test_every_example_in_the_catalog_becomes_its_tool_call():
    for rule in ACTIONS:
        for example in rule.examples:
            a = act(example)
            assert a is not None, f"{rule.name}: {example!r}"
            assert a.tool.startswith(rule.tool), f"{example!r} -> {a.tool}, not {rule.tool}"


def test_a_time_that_has_just_passed_means_tomorrow():
    now = datetime(2026, 10, 20, 12, 30, tzinfo=IST)
    assert act("remind me at 12 to eat", now).args["at"] == "2026-10-21T12:00"
    assert act("remind me at 12:45 pm to eat", now).args["at"] == "2026-10-20T12:45"
