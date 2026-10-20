import sys
import types

import numpy as np
import pytest

from minty_core.audio.stable import StablePrefix, Typewriter
from minty_core.audio.streaming import SherpaStreaming, build_streaming
from minty_core.config import AudioConfig
from minty_core.tui import TuiModel, caption_line


def test_words_settle_when_two_guesses_agree_and_never_go_back():
    sp = StablePrefix()
    assert sp.feed("recognise") == ("", "recognise")  # one guess: tentative only
    assert sp.feed("recognise speech") == ("recognise", "speech")
    assert sp.feed("wreck a nice beach today") == ("recognise", "")  # a flicker does not retract settled words
    assert sp.feed("recognise speech today please") == ("recognise", "speech today please")  # not yet agreed twice
    assert sp.feed("recognise speech today please now") == ("recognise speech today please", "now")
    assert sp.feed("recognise") == ("recognise speech today please", "")  # a shorter guess changes nothing
    assert sp.final("Recognise speech today, please.") == "Recognise speech today, please."


def test_punctuation_and_case_do_not_unsettle_words():
    sp = StablePrefix()
    sp.feed("hello, world")
    assert sp.feed("Hello world again")[0] == "Hello world"


def test_typewriter_reveals_one_word_at_a_time_then_the_tentative_tail():
    tw = Typewriter(per_second=10)
    tw.set("open the browser and find lamps", "under five hundred")
    out = [tw.step(t / 10) for t in range(0, 8)]
    counts = [len(s.split()) for s, _ in out]
    assert counts == sorted(counts) and counts[0] == 0 and counts[1] <= 1  # grows steadily from nothing
    assert all(t == "" for s, t in out if len(s.split()) < 5)  # the dim tail waits for the settled words
    s, t = tw.step(5.0)
    assert s == "open the browser and find lamps" and t == "under five hundred"
    tw.set("", "")
    assert tw.step(5.1) == ("", "")


def test_tui_caption_marks_the_newest_guess_dim():
    m = TuiModel()
    m.apply({"type": "partial", "text": "turn on the", "tentative": "kitchen light"})
    for t in range(0, 40):
        caption = caption_line(m, t / 10)
    assert caption.plain == "turn on the kitchen light …"
    spans = {caption.plain[s.start:s.end]: str(s.style) for s in caption.spans}
    assert any("dim" in style and "kitchen" in piece for piece, style in spans.items())
    m.apply({"type": "transcript", "role": "user", "text": "turn on the kitchen light"})
    assert caption_line(m, 5.0).plain == ""


class FakeRecognizer:
    def __init__(self):
        self.fed = 0
        self._ready = False

    def create_stream(self):
        return types.SimpleNamespace(waves=[], accept_waveform=self._accept)

    def _accept(self, rate, samples):
        assert rate == 16000 and samples.dtype == np.float32 and samples.max() <= 1.0
        self.fed += len(samples)
        self._ready = True

    def is_ready(self, stream):
        ready, self._ready = self._ready, False
        return ready

    def decode_stream(self, stream):
        pass

    def get_result(self, stream):
        return types.SimpleNamespace(text=" hello there " if self.fed else "")


def fake_sherpa(monkeypatch, recognizer):
    module = types.ModuleType("sherpa_onnx")
    module.OnlineRecognizer = types.SimpleNamespace(from_transducer=lambda **kw: recognizer)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)


def test_the_sherpa_adapter_feeds_pcm_and_returns_text(tmp_path, monkeypatch):
    rec = FakeRecognizer()
    fake_sherpa(monkeypatch, rec)
    for name in ("encoder-epoch-99.int8.onnx", "decoder-epoch-99.onnx", "joiner-epoch-99.int8.onnx", "tokens.txt"):
        (tmp_path / name).write_bytes(b"x")
    asr = SherpaStreaming(tmp_path)
    stream = asr.new_stream()
    assert asr.accept(stream, np.zeros(1280, dtype=np.int16).tobytes()) == "hello there" and rec.fed == 1280


def test_streaming_is_off_by_default_and_never_raises(tmp_path, monkeypatch):
    assert build_streaming(AudioConfig(), tmp_path) is None
    cfg = AudioConfig(streaming_asr="sherpa")
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)  # not installed
    assert build_streaming(cfg, tmp_path) is None
    fake_sherpa(monkeypatch, FakeRecognizer())
    assert build_streaming(cfg, tmp_path) is None  # model files missing


def test_captions_wait_for_a_slow_recogniser_instead_of_keeping_the_cpu_busy():
    from minty_core.audio.types import FRAME_S
    from minty_core.pipeline import caption_every
    assert caption_every(8, 0.1) == 8  # a fast recogniser: the configured interval
    slow = caption_every(8, 1.5)  # one caption takes 1.5 s
    assert slow * FRAME_S >= 3.0 - FRAME_S  # at most half of the time is spent on captions
    assert caption_every(8, 0.0) == 8 and caption_every(8, 40.0) <= 100  # never absurd
