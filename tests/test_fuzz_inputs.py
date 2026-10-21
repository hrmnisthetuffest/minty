"""Whatever is said or typed, parsing it never raises: a transcript can be garbage, half a sentence, another language or a huge number."""
import random
import string

import pytest

from minty_core.fastpath import match as fast_match
from minty_core.instant import Instant
from minty_core.profile_import import match_entry, parse_notes, Entry
from minty_core.selfupdate import parse_version
from minty_core.tui import parse_keys

WORDS = ("what", "is", "the", "time", "in", "plus", "minus", "times", "divided", "by", "to", "power", "of", "percent", "convert",
         "miles", "km", "celsius", "set", "timer", "for", "minutes", "remind", "me", "at", "volume", "play", "next", "spell",
         "flip", "coin", "roll", "dice", "date", "day", "tomorrow", "from", "square", "root", "factorial", "binary", "hex")
ODD = ["", " ", "\n", "\t", "0", "-0", "1e999", "9" * 400, "0" * 400, "½", "٣", "١٢٣", "∞", "nan", "inf", "ǅ", "\x00", "\x1b[", "😀", "a" * 2000,
       "((((((((((", "))))))))", "1/0", "0/0", "-1 to the power of 0.5", "10 to the power of 400", "1 divided by 0.0000000001", "2 ** 2 ** 2 ** 2"]


def random_text(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.4:
        return " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 12)))
    if kind < 0.6:
        return " ".join(rng.choice(WORDS + tuple(ODD) + tuple(str(rng.randint(-10**6, 10**6)) for _ in range(3))) for _ in range(rng.randint(1, 8)))
    if kind < 0.8:
        return "".join(chr(rng.choice((rng.randint(32, 126), rng.randint(0x80, 0x2FFF), rng.randint(0x1F300, 0x1F64F)))) for _ in range(rng.randint(0, 80)))
    return rng.choice(ODD) + rng.choice(WORDS) + rng.choice(ODD)


def test_the_instant_answers_never_raise_and_always_return_text_or_nothing():
    engine = Instant()
    rng = random.Random(20261021)
    for _ in range(4000):
        text = random_text(rng)
        hit = engine.answer(text)
        assert hit is None or (isinstance(hit.reply, str) and hit.reply.strip())


def test_the_page_command_matcher_never_raises():
    rng = random.Random(7)
    for _ in range(2000):
        fast_match(random_text(rng), None, wake_words=("minty",))


def test_key_parsing_never_raises_and_yields_keys_or_mouse_events():
    rng = random.Random(11)
    alphabet = string.printable + "\x1b[<0;12;34M\x1b[A\x7f\r\n" + "é😀"
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        list(parse_keys(text))


@pytest.mark.parametrize("text", ["", "v", "1", "1.0", "1.0.0.0", "v1.0.0-", "1.0.0-alpha", "1.0.0-alpha.", "-1.0.0", "1.0.0-rc.999999999999999999999",
                                  "１.０.１", "1.0.1\n", " 1.0.1 ", "x" * 5000, "1." + "0." * 2000])
def test_version_parsing_never_raises(text):
    assert parse_version(text) is None or len(parse_version(text)) == 5


def test_profile_notes_parsing_and_matching_never_raise():
    rng = random.Random(3)
    for _ in range(1500):
        lines = []
        for _ in range(rng.randint(0, 8)):
            lines.append(rng.choice(["- ", "", "  ", "* Q12 ", "## "]) + random_text(rng) + rng.choice([": ", "", ":", " : "]) + random_text(rng))
        for entry in parse_notes("\n".join(lines)):
            match_entry(entry)
    match_entry(Entry("", ""))
    match_entry(Entry("x" * 3000, "y" * 3000))


def test_a_huge_single_word_is_refused_at_once_instead_of_being_chewed_on():
    import time

    engine = Instant()
    for text in ("1" * 20000, " " * 20000 + "what time is it", "a" * 20000, "9" * 5000 + " plus 1"):
        started = time.perf_counter()
        assert engine.answer(text) is None
        assert time.perf_counter() - started < 0.1, text[:10]
    assert engine.answer("what is 7 plus 8").reply  # ordinary requests are unaffected
