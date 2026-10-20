"""Spoken progress (515), Hindi switching (522), the speaker check (508), read-aloud phrases (516 567)."""
import numpy as np
import pytest

from minty_core.voice.extras import VoiceExtras
from minty_core.voice.progress import Narrator
from minty_core.voice.speaker import SpeakerVerifier
from tests.test_pipeline import build


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def ev(job_id=1, status="running", progress="reading the second source", private=False, goal="research tides"):
    return {"type": "job", "id": job_id, "status": status, "progress": progress, "private": private, "goal": goal}


def test_narration_is_rate_limited_per_job_and_overall():
    clock = Clock()
    n = Narrator(per_job_s=60, overall_s=20, clock=clock)
    assert n.say(ev()) == "Still working on research tides: reading the second source."
    assert n.say(ev()) is None  # the same job again at once
    clock.t += 21
    assert n.say(ev(job_id=2, goal="check the weather", progress="asking for the forecast")) is not None
    clock.t += 10
    assert n.say(ev(job_id=3)) is None  # another job inside the overall gap
    clock.t += 70
    assert n.say(ev(progress="writing the summary")) is not None


def test_narration_skips_private_jobs_finished_jobs_and_empty_progress():
    n = Narrator(clock=Clock())
    assert n.say(ev(private=True)) is None
    assert n.say(ev(status="done")) is None and n.say(ev(status="failed")) is None
    assert n.say(ev(progress="")) is None
    assert n.say({"type": "notify"}) is None


def _extras(tmp_path):
    p, f = build(tmp_path, stt_texts=[], replies=[])
    return p.voice if p.voice else VoiceExtras(p.store, tmp_path), p


def test_hindi_is_understood_when_asked_and_english_only_puts_it_back(tmp_path):
    extras, p = _extras(tmp_path)
    reply = extras.intercept("switch to Hindi", "").reply
    assert "Hindi" in reply and p.store.config.stt.allowed_languages == ["en", "hi"]
    assert p.store.config.stt.language == ""  # let the recogniser pick between the two
    assert "voice" in reply.lower()  # it says plainly that it has no Hindi voice (none is assumed)
    extras.intercept("english only", "")
    assert p.store.config.stt.allowed_languages == ["en"] and p.store.config.stt.language == "en"


def test_the_speaker_check_accepts_you_and_rejects_the_other_voices():
    rng = np.random.default_rng(3)
    dim = 64
    voices = {name: rng.normal(size=dim) for name in ("you", "a", "b", "c", "d")}

    def sample(name):
        return voices[name] + rng.normal(scale=0.25, size=dim)

    verifier = SpeakerVerifier(extractor=lambda pcm: np.frombuffer(pcm, dtype=np.float64))
    verifier.enrol([sample("you").tobytes() for _ in range(5)])
    accepted = sum(verifier.verify(sample("you").tobytes()).ok for _ in range(100))
    false = sum(verifier.verify(sample(n).tobytes()).ok for n in ("a", "b", "c", "d") for _ in range(50))
    assert accepted >= 95 and false <= 4  # >= 95 % of yours, <= 2 % of 200 others


def test_without_a_model_it_is_unavailable_and_never_blocks_anything():
    v = SpeakerVerifier(extractor=None)
    assert v.available() is False
    r = v.verify(b"\x00\x00" * 100)
    assert r.ok is None and "no speaker model" in r.note and r.raise_scrutiny is False


def test_a_failed_check_only_raises_scrutiny(tmp_path):
    verifier = SpeakerVerifier(extractor=lambda pcm: np.frombuffer(pcm, dtype=np.float64))
    verifier.enrol([np.ones(8).tobytes()] * 3)
    other = verifier.verify((-np.ones(8)).tobytes())
    assert other.ok is False and other.raise_scrutiny is True  # ask more, never a lockout


@pytest.mark.parametrize("phrase, want", [
    ("read this page to me", "Read the current page aloud"),
    ("read the article", "Read the current page aloud"),
    ("read the page aloud", "Read the current page aloud"),
])
def test_reader_mode_asks_for_the_main_text_only(tmp_path, phrase, want):
    extras, p = _extras(tmp_path)
    act = extras.intercept(phrase, "")
    assert act is not None and want in act.text and "main article text" in act.text


def test_reading_a_dictated_note_back(tmp_path):
    extras, p = _extras(tmp_path)
    extras._last_note = "buy milk and eggs"
    assert extras.intercept("read that back", "").reply == "buy milk and eggs"
    extras._last_note = ""
    assert "nothing" in extras.intercept("read that back", "").reply.lower()
