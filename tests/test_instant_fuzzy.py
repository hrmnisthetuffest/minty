"""Spelling slips in a spoken command still reach the instant actions, and a repair can never become a different, riskier command."""
import pytest

from minty_core.instant.actions import ACTIONS, match
from minty_core.instant.fuzzy import distance, repair, vocabulary_of
from tests.instant_fakes import NOW

WAKE = ("minty",)


def act(text, **kw):
    return match(text, WAKE, NOW, **kw)


@pytest.mark.parametrize("a,b,d", [("volume", "volume", 0), ("volumn", "volume", 1), ("pasue", "pause", 1), ("pause", "paus", 1),
                                   ("timer", "tymer", 1), ("music", "musik", 1), ("abc", "xyz", 3), ("", "ab", 2)])
def test_distance_counts_a_swap_as_one_edit(a, b, d):
    assert distance(a, b) == d


def test_repair_changes_only_a_clear_near_miss_and_says_nothing_when_it_changed_nothing():
    vocab = frozenset({"volume", "pause", "music", "timer"})
    assert repair("turn the volumn down", vocab) == "turn the volume down"
    assert repair("pasue the musik", vocab) == "pause the music"
    assert repair("turn the volume down", vocab) is None  # nothing to repair
    assert repair("pause the dog", vocab) is None
    assert repair("volumn pasue musik timr", vocab) is None  # more than two repairs is a different sentence, not a slip


def test_a_word_equally_close_to_two_command_words_is_left_alone():
    assert repair("the bound", frozenset({"sound", "round"})) is None  # one edit from either
    assert repair("the soun", frozenset({"sound", "sounds"})) == "the sound"  # a word and its plural are one word
    assert repair("the sound", frozenset({"sound", "round"})) is None  # already a command word


@pytest.mark.parametrize("said,tool,args", [
    ("set a time for 5 minutes", "timers_set_timer", {"seconds": 300, "label": ""}),
    ("set a tymer for ten minutes", "timers_set_timer", {"seconds": 600, "label": ""}),
    ("Minty, set a timer for 5 minuts", "timers_set_timer", {"seconds": 300, "label": ""}),
    ("turn the volumn down", "system_control", {"action": "volume", "value": "down"}),
    ("make it loudar", "system_control", {"action": "volume", "value": "up"}),
    ("set the volume to 40", "system_control", {"action": "volume", "value": "40"}),
    ("pause the musik", "spotify_pause", {}),
    ("pasue the music", "spotify_pause", {}),
    ("resum the music", "spotify_resume", {}),
    ("unmuet the sound", "system_control", {"action": "unmute"}),
    ("mute the sond", "system_control", {"action": "mute"}),
    ("set a pasta time for twelve minutes", "timers_set_timer", {"seconds": 720, "label": "pasta"}),
])
def test_spelling_slips_reach_the_command(said, tool, args):
    a = act(said)
    assert a is not None and a.tool == tool and a.args == args


def test_a_repaired_hit_says_what_it_understood():
    a = act("turn the volumn down")
    assert a.heard_as == "turn the volume down"
    assert act("turn the volume down").heard_as == ""


@pytest.mark.parametrize("said", [
    "ship it",  # "skip" is short and common: it is never a repair target
    "lok the screen", "lick the screen", "loc the computer", "lock the scren",  # a repair can never produce a lock
    "pause the dog", "what is the time", "set a timer for banana", "play something", "send a message to mom", "delete my files",
])
def test_things_that_are_not_commands_stay_with_the_model(said):
    assert act(said) is None


def test_exact_commands_are_never_changed_by_the_repair():
    for rule in ACTIONS:
        for example in rule.examples:
            assert act(example) == act(example, fuzzy=False), example
            assert act(example).heard_as == "", example


def test_number_words_are_not_mistaken_for_command_words():
    a = act("set a tymer for four minutes")
    assert a is not None and a.args["seconds"] == 240  # "four" must not become "hour"


@pytest.mark.parametrize("said", ["what is the time", "what time is it", "whats the time", "any timres running", "how much timr is left"])
def test_a_repair_never_turns_a_question_about_the_time_into_a_timer_question(said):
    a = act(said)
    assert a is None or a.tool != "timers_list_timers"


def test_the_switch_turns_the_repair_off():
    assert act("turn the volumn down", fuzzy=False) is None
    assert act("turn the volumn down") is not None


def test_the_vocabulary_comes_from_the_patterns_and_leaves_out_lock_and_short_words():
    import re
    vocab = vocabulary_of([re.compile(r"^(?:turn |make )?(?:the )?(?:volume|sound) (?:up|down)$"), re.compile(r"^lock (?:the )?screen$")], extra=())
    assert "volume" in vocab and "sound" in vocab and "turn" not in vocab  # words under five letters are never targets
    assert "screen" in vocab  # only the patterns handed in count; the module hands in the non-lock ones
