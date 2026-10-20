import json

import pytest

from minty_core.research.claims import Claim, cross_check, extract, parse_claims, verified

CHUNKS = [
    "The Great Barrier Reef is the world's largest coral reef system. It stretches for about 2,300 kilometres "
    "along the coast of Queensland, Australia.",
    "Coral bleaching happens when water is too warm. Mass bleaching was recorded in 1998, 2002 and 2016.",
]


def test_quotes_must_be_found_word_for_word():
    assert verified("It stretches for about 2,300 kilometres along the coast of Queensland, Australia.", CHUNKS[0])
    assert verified("it STRETCHES for about 2,300 kilometres  along the coast", CHUNKS[0])  # case/space only
    assert not verified("It stretches for about 3,000 kilometres along the coast", CHUNKS[0])
    assert not verified("short", CHUNKS[0])


def test_invented_citations_are_dropped_and_real_ones_kept():
    reply = json.dumps([
        {"claim": "The reef is about 2,300 km long", "quote": "It stretches for about 2,300 kilometres along the "
         "coast of Queensland, Australia.", "chunk": 0},
        {"claim": "The reef is 5,000 km long", "quote": "The reef stretches for five thousand kilometres in total.",
         "chunk": 0},
        {"claim": "Bleaching in 2016", "quote": "Mass bleaching was recorded in 1998, 2002 and 2016.", "chunk": 0},
        "junk", {"claim": "no quote"},
    ])
    got = parse_claims("Sure! " + reply + " done", "S1", CHUNKS)
    assert [c.text for c in got] == ["The reef is about 2,300 km long", "Bleaching in 2016"]
    assert got[1].chunk == 1  # the quote was not in the chunk named, so it was found in the right one


def test_garbage_replies_give_no_claims():
    for reply in ("", "I cannot help", "{not a list}", "[1, 2"):
        assert parse_claims(reply, "S1", CHUNKS) == []


async def test_extract_uses_the_model_and_maps_chunk_numbers_back():
    seen = []

    async def ask(system, user):
        seen.append(user)
        return json.dumps([{"claim": "Bleached in 2016", "chunk": 1,
                            "quote": "Mass bleaching was recorded in 1998, 2002 and 2016."}])

    claims, used = await extract(ask, "coral bleaching history", "S2", CHUNKS)
    assert used and len(claims) == 1 and claims[0].chunk == 1 and claims[0].source == "S2"
    assert "Question: coral bleaching history" in seen[0] and "[chunk 0]" in seen[0]


async def test_extract_falls_back_to_plain_sentences_without_a_model():
    async def down(system, user):
        raise RuntimeError("no model")

    claims, used = await extract(down, "coral reef bleaching warm water", "S1", CHUNKS)
    assert not used and claims and all(c.text == c.quote and "unparaphrased" in c.flags for c in claims)
    none, used = await extract(None, "zzz qqq", "S1", CHUNKS)
    assert none == [] and not used


def test_cross_check_counts_support_and_flags_number_clashes():
    a = Claim("The reef is about 2300 kilometres long", "q" * 30, "S1", 0)
    b = Claim("The reef is roughly 2300 kilometres long along Queensland", "q" * 30, "S2", 0)
    c = Claim("The reef is about 1400 kilometres long", "q" * 30, "S3", 0)
    d = Claim("Coral spawning happens after a full moon in spring", "q" * 30, "S4", 0)
    clashes = cross_check([a, b, c, d])
    assert a.support() == 2 and b.corroborated_by == ["S1"]
    assert "contradicted" in c.flags and "contradicted" in a.flags and len(clashes) >= 2
    assert d.flags == ["single source"] and d.support() == 1
