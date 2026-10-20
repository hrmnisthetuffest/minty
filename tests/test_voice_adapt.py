"""Pronunciations, whisper mode, ambient volume, pace, time of day, ducking (519 511 741 742 521 740)."""
import asyncio
from datetime import datetime

import numpy as np

from tests.test_pipeline import build, finish, wait_until
from minty_core.audio.types import Audio
from minty_core.voice.adapt import (Ducker, Pronunciations, ambient_gain, pace_factor, time_profile)
from minty_core.voice.extras import VoiceExtras


def test_a_pronunciation_replaces_whole_words_only_and_keeps_case_where_it_can(tmp_path):
    p = Pronunciations(tmp_path / "pron.json")
    p.add("Paris", "Pa-ree")
    p.add("SQL", "sequel")
    assert p.apply("The weather in Paris, and in paris again; SQL is fun.") == \
        "The weather in Pa-ree, and in Pa-ree again; sequel is fun."
    assert p.apply("Pariss are not a place") == "Pariss are not a place"
    assert Pronunciations(tmp_path / "pron.json").entries() == {"paris": "Pa-ree", "sql": "sequel"}
    p.remove("sql")
    assert "sql" not in p.entries()


def test_the_phrases_that_teach_it():
    from minty_core.voice.adapt import parse_pronunciation

    assert parse_pronunciation("say Paris as Pa-ree") == ("Paris", "Pa-ree")
    assert parse_pronunciation("pronounce SQL as sequel") == ("SQL", "sequel")
    assert parse_pronunciation("what time is it") is None


def test_ambient_gain_follows_the_room_and_stays_in_bounds():
    quiet, normal, loud = ambient_gain(-62), ambient_gain(-48), ambient_gain(-30)
    assert quiet < normal < loud and 0.6 <= quiet and loud <= 1.6
    assert ambient_gain(None) == 1.0


def test_pace_follows_your_speaking_speed_within_limits():
    assert pace_factor(2.5) == 1.0
    assert pace_factor(3.5) > 1.0 > pace_factor(1.5)
    assert 0.85 <= pace_factor(0.3) and pace_factor(9) <= 1.15
    assert pace_factor(None) == 1.0


def test_the_evening_profile_applies_only_inside_its_hours():
    profiles = [{"from": "21:30", "to": "06:30", "gain": 0.8, "speed": 0.95}]
    assert time_profile(profiles, datetime(2026, 10, 15, 23, 0)) == {"gain": 0.8, "speed": 0.95}
    assert time_profile(profiles, datetime(2026, 10, 15, 3, 0))["gain"] == 0.8
    assert time_profile(profiles, datetime(2026, 10, 15, 12, 0)) == {"gain": 1.0, "speed": 1.0}
    assert time_profile([], datetime(2026, 10, 15, 23, 0)) == {"gain": 1.0, "speed": 1.0}


SINK_INPUTS = """Sink Input #31
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.name = "Spotify"
\t\tapplication.process.id = "2222"
Sink Input #44
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.name = "minty"
\t\tapplication.process.id = "4242"
"""


def test_ducking_lowers_other_apps_and_puts_them_back_never_touching_its_own():
    calls = []

    def run(argv):
        calls.append(argv)
        return (0, SINK_INPUTS) if argv[:3] == ["pactl", "list", "sink-inputs"] else (0, "")

    d = Ducker(run, own_pid=4242, level=0.3)
    assert d.begin() is True
    assert ["pactl", "set-sink-input-volume", "31", "30%"] in calls
    assert not any(c[:2] == ["pactl", "set-sink-input-volume"] and c[2] == "44" for c in calls)
    assert d.begin() is False  # already ducked
    d.restore()
    assert ["pactl", "set-sink-input-volume", "31", "100%"] in calls
    d.restore()  # nothing left to restore
    assert sum(1 for c in calls if c[:2] == ["pactl", "set-sink-input-volume"]) == 2


def _pipeline(tmp_path, **voice):
    p, f = build(tmp_path, stt_texts=["where is Paris"], replies=[["Paris is near Lyon."]])
    data = p.store.config.model_dump(mode="json")
    data["voice"].update(voice)
    p.store.update(data)
    p.voice = VoiceExtras(p.store, tmp_path)
    return p, f


async def test_what_is_spoken_is_respelled_but_what_is_shown_is_the_real_sentence(tmp_path):
    p, f = _pipeline(tmp_path, pronunciations=True)
    p.voice.pronunciations.add("Paris", "Pa-ree")
    events = f.bus.subscribe({"transcript"})
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.tts.calls)
    await finish(p, f, task)
    assert f.tts.calls == ["Pa-ree is near Lyon."]
    shown = [e["text"] for e in list(events._queue) if e.get("role") == "assistant"]
    assert shown == ["Paris is near Lyon."]


async def test_whisper_mode_lowers_the_level_and_off_leaves_audio_alone(tmp_path):
    loud = Audio((np.ones(800) * 8000).astype(np.int16).tobytes(), 16000)

    class LoudTTS:
        calls = []

        async def synthesize(self, text):
            return loud

    for whisper in (False, True):
        p, f = _pipeline(tmp_path / str(whisper), whisper=whisper)
        p.tts = LoudTTS()
        f.audio.push(b"WAKE", b"SPEECH", b"END")
        task = asyncio.create_task(p.run())
        await wait_until(lambda: f.player.played)
        await finish(p, f, task)
        level = int(np.abs(np.frombuffer(f.player.played[0], dtype=np.int16)).max())
        assert (level < 4000) if whisper else (level == 8000)
