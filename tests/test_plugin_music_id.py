import numpy as np
import pytest

from tests.plugin_helpers import make_host

LOUD = (np.sin(np.arange(44100 * 8) / 10) * 8000).astype(np.int16).tobytes()


def setup(tmp_path, monkeypatch, *, playing=None, computer=False, pcm=LOUD, found=("Blinding Lights", "The Weeknd"),
          watcher=None):
    host = make_host(tmp_path)
    tool = host.get_tool("music_id_identify")
    g = tool.func.__globals__
    calls = {"record": [], "shazam": 0, "paused": None}

    async def fake_now_playing(paused):
        calls["paused"] = paused
        return playing

    async def fake_computer():
        return computer

    async def fake_record(device, seconds):
        calls["record"].append(device)
        return pcm

    async def fake_shazam(wav):
        calls["shazam"] += 1
        assert wav[:4] == b"RIFF"
        if isinstance(found, Exception):
            raise found
        return found

    for name, fn in (("now_playing", fake_now_playing), ("computer_is_playing", fake_computer),
                     ("record", fake_record), ("shazam", fake_shazam)):
        monkeypatch.setitem(g, name, fn)
    if watcher is not None:
        host._services["music_watcher"] = watcher
    return host, calls


async def test_a_playing_app_answers_instantly_without_listening(tmp_path, monkeypatch):
    host, calls = setup(tmp_path, monkeypatch, playing=("On Sight", "Kanye West", "spotify"))
    assert await host.call("music_id_identify", {}) == "That's On Sight by Kanye West, playing in Spotify."
    assert calls["record"] == [] and calls["shazam"] == 0


async def test_players_minty_paused_for_the_conversation_still_count(tmp_path, monkeypatch):
    from types import SimpleNamespace

    host, calls = setup(tmp_path, monkeypatch, playing=("9", "Drake", "firefox.instance_1_81"),
                        watcher=SimpleNamespace(paused=["firefox.instance_1_81"]))
    assert await host.call("music_id_identify", {}) == "That's 9 by Drake, playing in your browser."
    assert calls["paused"] == ["firefox.instance_1_81"]


async def test_computer_sound_is_recorded_from_the_output_not_the_microphone(tmp_path, monkeypatch):
    host, calls = setup(tmp_path, monkeypatch, computer=True)
    assert await host.call("music_id_identify", {}) == "That's Blinding Lights by The Weeknd."
    assert calls["record"] == ["@DEFAULT_MONITOR@"]


async def test_music_in_the_room_is_heard_through_the_microphone(tmp_path, monkeypatch):
    host, calls = setup(tmp_path, monkeypatch)
    assert await host.call("music_id_identify", {}) == "That's Blinding Lights by The Weeknd."
    assert calls["record"] == ["@DEFAULT_SOURCE@"]


async def test_silence_and_no_match_and_failures_are_explained(tmp_path, monkeypatch):
    host, calls = setup(tmp_path, monkeypatch, pcm=bytes(44100 * 2))
    assert await host.call("music_id_identify", {}) == "I can't hear any music right now."
    assert calls["shazam"] == 0
    host, _ = setup(tmp_path / "b", monkeypatch, found=None)
    assert "couldn't recognise it" in await host.call("music_id_identify", {})
    host, _ = setup(tmp_path / "c", monkeypatch, found=RuntimeError("blocked"))
    assert (await host.call("music_id_identify", {})).startswith("Error: song recognition is unavailable")


async def test_asking_about_apps_only_never_records(tmp_path, monkeypatch):
    host, calls = setup(tmp_path, monkeypatch)
    assert "None of your music apps" in await host.call("music_id_identify", {"source": "app"})
    assert calls["record"] == []


def test_wav_wrapper_and_rms():
    from minty_core.plugins.builtin.music_id.plugin import _rms, to_wav

    wav = to_wav(LOUD[:1000])
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert _rms(bytes(100)) == 0.0 and _rms(LOUD) > 1000
