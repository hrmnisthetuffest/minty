import pytest

from minty_core.audio.gate import judge


@pytest.mark.parametrize("text, kw", [
    ("stop", {"voiced_ms": 400}),
    ("yes", {"voiced_ms": 300}),
    ("Next song.", {"voiced_ms": 600}),
    ("What time is it?", {"voiced_ms": 1100}),
    ("Thank you, that was helpful.", {"voiced_ms": 1500}),
    ("thank you", {"voiced_ms": 1200, "no_speech_prob": 0.05, "avg_logprob": -0.2}),  # said clearly
    ("you", {"voiced_ms": 1000, "no_speech_prob": 0.02, "avg_logprob": -0.1}),
    ("Play Kanye West", {}),  # no detector available: trust the text
    ("okay bye", {"voiced_ms": 700}),
    ("um what's the weather", {"voiced_ms": 1300}),
])
def test_real_speech_passes(text, kw):
    assert judge(text, **kw).accept, text


@pytest.mark.parametrize("text, kw, reason", [
    ("", {}, "no words"),
    ("   .  ", {}, "no words"),
    ("[Music]", {}, "sound tags only"),
    ("(applause) [BLANK_AUDIO]", {}, "sound tags only"),
    ("Thanks for watching!", {"voiced_ms": 300}, "typical mis-hearing"),
    ("Thank you.", {"voiced_ms": 600}, "typical mis-hearing"),
    ("you", {"no_speech_prob": 0.5}, "typical mis-hearing"),
    ("hmm", {"avg_logprob": -1.0}, "typical mis-hearing"),
    ("open the door", {"voiced_ms": 100}, "ms of speech"),
    ("turn it up", {"no_speech_prob": 0.8, "avg_logprob": -1.4}, "unsure"),
    ("the the the the the the the the", {}, "repetition"),
    ("I I I I I I I I I", {"voiced_ms": 2000}, "repetition"),
])
def test_noise_shaped_transcripts_are_dropped(text, kw, reason):
    verdict = judge(text, **kw)
    assert not verdict.accept and reason in verdict.reason, (text, verdict)
