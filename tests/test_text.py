from minty_core.text import SentenceSplitter


def test_splits_across_chunks():
    s = SentenceSplitter()
    assert s.feed("Hello the") == []
    assert s.feed("re. How are") == ["Hello there."]
    assert s.flush() == "How are"
    assert s.flush() is None


def test_ellipsis_question_and_decimals():
    s = SentenceSplitter()
    assert s.feed("Wait... what? It is 3.5 degrees. Yes.") == ["Wait...", "what?", "It is 3.5 degrees."]
    assert s.flush() == "Yes."


def test_no_terminator_yields_nothing_until_flush():
    s = SentenceSplitter()
    assert s.feed("no end yet") == []
    assert s.flush() == "no end yet"


def test_punctuation_only_fragments_are_never_spoken():
    s = SentenceSplitter()
    assert s.feed("Four. . ... Done. ") == ["Four.", "Done."]
    assert s.feed(".") == [] and s.flush() is None
