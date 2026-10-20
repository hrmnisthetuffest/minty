"""The first words of an answer are spoken as soon as there is a natural place to stop, not only at the end of the first sentence."""
import pytest

from minty_core.config import Config
from minty_core.text import SentenceSplitter

LONG = "The capital of France is Paris, which sits on the Seine and has about two million people. It is lovely in spring."


def run(splitter, text, size=7):
    out = []
    for i in range(0, len(text), size):
        out += splitter.feed(text[i:i + size])
    tail = splitter.flush()
    return out + ([tail] if tail else [])


def test_the_first_chunk_ends_at_the_first_comma_once_there_are_enough_words():
    assert run(SentenceSplitter(first_clause_words=6), LONG) == [
        "The capital of France is Paris,", "which sits on the Seine and has about two million people.", "It is lovely in spring."]


def test_the_default_is_the_old_behaviour():
    assert run(SentenceSplitter(), LONG) == [
        "The capital of France is Paris, which sits on the Seine and has about two million people.", "It is lovely in spring."]


def test_a_short_start_is_not_cut():
    assert run(SentenceSplitter(first_clause_words=6), "Sure, I can do that. Then more words follow here.") == [
        "Sure, I can do that.", "Then more words follow here."]


def test_only_the_very_first_chunk_is_cut():
    text = "One two three four five six, seven eight nine ten eleven twelve, thirteen fourteen fifteen sixteen. Next, again and again and again."
    assert run(SentenceSplitter(first_clause_words=6), text) == [
        "One two three four five six,", "seven eight nine ten eleven twelve, thirteen fourteen fifteen sixteen.", "Next, again and again and again."]


@pytest.mark.parametrize("text, first", [
    ("It costs 1,200 rupees, which is a lot more than the earlier price.", "It costs 1,200 rupees,"),  # 1,200 is a number, not a pause
    ("Here is the list: apples, pears and plums are all in the bowl today.", "Here is the list:"),
    ("Well I think that we could go there, but only if it stays dry.", "Well I think that we could go there,"),
])
def test_where_the_first_piece_stops(text, first):
    assert run(SentenceSplitter(first_clause_words=4), text)[0] == first


def test_a_dash_after_too_few_words_is_not_a_place_to_stop():
    out = run(SentenceSplitter(first_clause_words=4), "Well — that is a very long way to go for a coffee, honestly.")
    assert out[0].startswith("Well — that is")  # not "Well —"
    assert out[0] != "Well —"


@pytest.mark.parametrize("text", [
    "The answer (which took a while, honestly) is forty two and that is final.",
    'He called it "a long, strange trip, really" and left the room at once.',
])
def test_a_pause_inside_brackets_or_quotes_is_not_a_place_to_stop(text):
    assert run(SentenceSplitter(first_clause_words=3), text) == [text]


def test_nothing_is_lost_or_reordered():
    text = "One two three four five six seven, eight nine ten eleven twelve thirteen fourteen; fifteen sixteen. Then, finally, a last one."
    for size in (1, 3, 11, 50):
        pieces = run(SentenceSplitter(first_clause_words=5), text, size)
        assert " ".join(pieces) == text


def test_the_setting_exists_and_is_on_by_default():
    cfg = Config().turn
    assert cfg.first_clause_words == 8
