import numpy as np

import minty_core.audio.stt as stt
from minty_core.app import preload_voice_models
from minty_core.config import ConfigStore


class Model:
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, **kw):
        assert isinstance(audio, np.ndarray)
        self.calls += 1
        return iter([]), None


def test_preload_loads_and_warms_the_configured_model(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    store.load()
    model, loaded = Model(), []
    monkeypatch.setattr(stt, "_load_whisper", lambda name: loaded.append(name) or model)
    seconds = preload_voice_models(store)
    assert loaded == [store.config.stt.local_model] and model.calls == 1 and seconds >= 0


def test_preload_survives_a_missing_model(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    store.load()

    def boom(name):
        raise RuntimeError("no network")

    monkeypatch.setattr(stt, "_load_whisper", boom)
    assert preload_voice_models(store) >= 0  # logs a warning, never raises
