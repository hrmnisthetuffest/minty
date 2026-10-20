import random

import pytest

from minty_core.instant.fun import COMPLIMENTS, EIGHT_BALL, FACTS, JOKES, QUOTES, TWISTERS
from tests.instant_fakes import say


def fresh(seed=1):
    import random as _r
    from minty_core.instant import Env, Instant
    from tests.instant_fakes import NOW

    return Instant(Env(now=lambda: NOW, rng=_r.Random(seed)))


def test_a_coin_gives_heads_and_tails_about_evenly():
    e = fresh()
    results = [say(e, "flip a coin") for _ in range(200)]
    assert set(results) == {"Heads.", "Tails."} and 70 < results.count("Heads.") < 130


def test_dice_stay_in_range_and_add_up():
    e = fresh()
    for _ in range(100):
        one = say(e, "roll a die")
        assert one.startswith("You rolled a") or one.startswith("You rolled an")
        n = int(one.split()[-1].rstrip("."))
        assert 1 <= n <= 6
    two = say(e, "roll two dice")
    a, b = (int(x) for x in two.replace(",", "").split()[2:5:2])
    assert two.endswith(f"for a total of {a + b}.")
    assert 3 <= int(say(e, "roll 3d6").rsplit(" ", 1)[1].rstrip(".")) <= 18


@pytest.mark.parametrize("said, ok", [("roll a d20", True), ("roll a d1", False), ("roll 11 dice", False), ("roll a d5000", False)])
def test_dice_limits(said, ok):
    assert (say(fresh(), said) is not None) == ok


def test_articles_read_naturally():
    e = fresh()
    seen = {say(e, "roll a d100") for _ in range(400)}
    assert any(s.startswith("You rolled an 8") for s in seen) and any(s.startswith("You rolled a 7") for s in seen)
    assert not any(s.startswith("You rolled a 8 ") or s.startswith("You rolled a 11 ") for s in seen)


def test_random_numbers_respect_the_range_in_either_order():
    e = fresh()
    for _ in range(100):
        assert 5 <= int(say(e, "pick a number between 5 and 9").rsplit(" ", 1)[1].rstrip(".")) <= 9
        assert 5 <= int(say(e, "random number between 9 and 5").rsplit(" ", 1)[1].rstrip(".")) <= 9
    assert say(e, "random number between 1 and 99999999999") is None


def test_picks():
    e = fresh()
    assert say(e, "pick a card").endswith(("Spades.", "Hearts.", "Diamonds.", "Clubs."))
    assert len(say(e, "random letter")) == 2
    assert say(e, "give me a random colour").rstrip(".").lower() in [c for c in __import__("minty_core.instant.fun", fromlist=["COLOURS"]).COLOURS]
    assert say(e, "yes or no") in ("Yes.", "No.")
    assert say(e, "ask the magic 8 ball will I pass") in EIGHT_BALL


@pytest.mark.parametrize("said, options", [
    ("pick between pizza and burgers", {"pizza", "burgers"}), ("choose between red, blue or green", {"red", "blue", "green"}),
    ("decide between tea or coffee", {"tea", "coffee"}), ("pick one of sleep, study or games", {"sleep", "study", "games"}),
])
def test_choosing_picks_one_of_the_options(said, options):
    picks = {say(fresh(seed), said).removeprefix("I pick ").rstrip(".") for seed in range(40)}
    assert picks == options


@pytest.mark.parametrize("said", ["pick up the kids and the dog", "choose a restaurant", "pick pizza", "select all and delete",
                                  "pick a song or two for the party tonight at my house with friends"])
def test_errands_and_single_things_are_not_a_choice(said):
    assert say(fresh(), said) is None


def test_the_lists_of_things_to_say_are_long_clean_and_unique():
    for items, low in ((JOKES, 25), (FACTS, 25), (QUOTES, 12), (COMPLIMENTS, 6), (TWISTERS, 6), (EIGHT_BALL, 20)):
        assert len(items) >= low and len(set(items)) == len(items) and all(i[0].isupper() or i[0] == "“" for i in items)


def test_jokes_and_facts_vary():
    e = fresh()
    assert len({say(e, "tell me a joke") for _ in range(40)}) > 10
    assert len({say(e, "tell me a fun fact") for _ in range(40)}) > 10
    assert say(e, "tell me a joke about cats") is None and say(e, "tell me a story") is None


@pytest.mark.parametrize("said, reply", [
    ("count to 5", "1, 2, 3, 4, 5."), ("count down from 3", "3, 2, 1."), ("count to 500", None), ("count down from 0", None),
    ("how do you spell necessary", "N, E, C, E, S, S, A, R, Y."), ("spell don't", "D, O, N, T."), ("spell check", None),
    ("say the alphabet", "A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P, Q, R, S, T, U, V, W, X, Y, Z."),
])
def test_counting_and_spelling(said, reply):
    assert say(fresh(), said) == reply
