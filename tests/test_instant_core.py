"""Instant answers, the shared parts: cleaning what was said, number words, saying numbers, and the engine's rules of the road."""
import random
from datetime import datetime

import pytest

from minty_core.instant import Env, Instant, Rule
from minty_core.instant.text import clean, num, parse_duration, plural, say_duration, words_to_digits
from tests.instant_fakes import NOW, engine


def make(**kw) -> Instant:
    return Instant(Env(now=lambda: NOW, rng=random.Random(7), **kw))


@pytest.mark.parametrize("said, cleaned", [
    ("Hey Minty, what's the time?", "what is the time"),
    ("Minty what time is it please", "what time is it"),
    ("Okay, so can you tell me the date", "the date"),
    ("what's 5 plus 5 right now", "what is 5 plus 5"),
    ("What’s the date", "what is the date"),
    ("it's 1,200 rupees", "it is 1200 rupees"),
    ("set a timer for 5 p.m.", "set a timer for 5 pm"),
    ("Mindy... how are you?", "how are you"),
    ("   ", ""),
])
def test_clean_strips_the_wake_word_fillers_and_punctuation(said, cleaned):
    assert clean(said, ("minty", "mindy")) == cleaned


@pytest.mark.parametrize("said, digits", [
    ("twelve plus seven", "12 plus 7"),
    ("one hundred twenty five", "125"),
    ("two thousand twenty six", "2026"),
    ("a hundred and five", "105"),
    ("three point five", "3.5"),
    ("minus five", "-5"),
    ("negative twelve", "-12"),
    ("fifteen percent of two hundred forty", "15 percent of 240"),
    ("pick one of these", "pick 1 of these"),
    ("twelve minus five", "12 minus 5"),
    ("minus five plus two", "-5 plus 2"),
    ("a dog", "a dog"),
    ("two million three hundred thousand", "2300000"),
])
def test_number_words_become_digits(said, digits):
    assert words_to_digits(said) == digits


@pytest.mark.parametrize("value, said", [
    (19, "19"), (1200, "1,200"), (-7, "-7"), (3.0, "3"), (2.5, "2.5"), (0.333333333, "0.333333"), (1234567.891, "1,234,567.89"),
    (1 / 3, "0.333333"), (12.3456789, "12.3457"), (1e18, "1 times 10 to the 18"), (4.0e20, "4 times 10 to the 20"), (-1.5e16, "-1.5 times 10 to the 16"),
])
def test_numbers_are_said_plainly(value, said):
    assert num(value) == said


def test_plural():
    assert plural(1, "day") == "1 day" and plural(2, "day") == "2 days" and plural(0, "hour") == "0 hours"


def test_the_first_rule_that_answers_wins_and_a_failing_rule_is_skipped():
    def boom(q):
        raise RuntimeError("bad rule")

    engine = Instant(Env(now=lambda: NOW), rules=[
        Rule("boom", "test", ("x",), boom), Rule("one", "test", ("x",), lambda q: "first" if q.cmd == "x" else None),
        Rule("two", "test", ("x",), lambda q: "second")])
    hit = engine.answer("x")
    assert hit.reply == "first" and hit.rule == "one" and hit.category == "test"


def test_long_or_multi_line_requests_are_left_to_the_model():
    engine = make()
    assert engine.answer("what time is it") is not None
    assert engine.answer("what time is it and then tell me a story about a dragon who lost his hat in the snow") is None
    assert engine.answer("what time is it\nand the date") is None


def test_categories_can_be_left_to_the_model():
    assert make().answer("what time is it").category == "time and date"
    assert Instant(Env(now=lambda: NOW), skip=("time and date",)).answer("what time is it") is None


def test_nothing_or_only_the_wake_word_is_not_an_answer():
    engine = make(wake_words=("minty",))
    assert engine.answer("") is None and engine.answer("Minty") is None and engine.answer("hey minty.") is None


def test_the_wake_word_is_taken_off_the_front():
    assert make(wake_words=("minty", "mindy")).answer("Hey Mindy, what time is it?").reply == "It's 4:32 PM."


def test_every_example_in_the_catalog_is_answered_by_its_own_rule(tmp_path):
    from minty_core.instant import RULES

    eng = engine(tmp_path)
    names = [r.name for r in RULES]
    assert len(names) == len(set(names)), "rule names must be unique"
    for rule in RULES:
        assert rule.examples, rule.name
        for example in rule.examples:
            hit = eng.answer(example)
            assert hit is not None, f"{rule.name}: {example!r} was not answered"
            assert hit.rule == rule.name, f"{example!r} was answered by {hit.rule}, not {rule.name}"


def test_the_catalog_is_long():
    from minty_core.instant import RULES

    assert sum(len(r.examples) for r in RULES) >= 250


@pytest.mark.parametrize("said, seconds", [
    ("5 minutes", 300), ("90 seconds", 90), ("an hour and a half", 5400), ("half an hour", 1800), ("1 hour 30 minutes", 5400),
    ("2 hours and 15 minutes", 8100), ("a minute", 60), ("quarter of an hour", 900), ("1.5 hours", 5400),
])
def test_durations_are_read(said, seconds):
    assert parse_duration(said) == seconds


@pytest.mark.parametrize("said", ["", "soon", "5", "5 minutes later than that", "for ever"])
def test_other_words_are_not_a_duration(said):
    assert parse_duration(said) is None


@pytest.mark.parametrize("seconds, said", [(45, "45 seconds"), (60, "1 minute"), (90, "1 minute 30 seconds"), (5400, "1 hour 30 minutes"),
                                           (86400 + 3600, "1 day 1 hour"), (0, "0 seconds")])
def test_durations_are_said(seconds, said):
    assert say_duration(seconds) == said


@pytest.mark.parametrize("request_", [
    "what is the time complexity of quicksort", "set the time on my oven", "what time does the store close",
    "what is the date of the next exam", "tell me about the history of christmas", "how many days until my birthday",
    "how many days until the exam", "who is the president of india", "what is the weather today", "play some music",
    "pick up the kids and the dog", "what is a prime number", "what is a leap year", "calculate my taxes", "what is 5 plus",
    "what is the capital gains tax", "convert this file to pdf", "how much does it cost to fly to paris",
    "how many people live in india", "what is the weather in tokyo", "why is the sky blue", "tell me a story about a dragon",
    "what is the best way to learn python", "write a joke about cats", "can you tell me a joke about programmers",
    "remind me tomorrow at 9 to call mom", "what is the square root of two million plus my age", "open the file manager",
    "what time is my meeting", "what is the speed of my internet", "i feel sad", "what do you think about the election",
    "turn on the lights", "who won the match yesterday", "what is the price of bitcoin", "translate hello into french",
])
def test_requests_that_need_a_model_are_left_alone(request_):
    assert make().answer(request_) is None
