"""WP-08 acceptance (verified with the pipeline fakes; nothing here has been heard with your voice)."""
import asyncio

import numpy as np
import pytest

from tests.test_pipeline import build, finish, wait_until
from minty_core.config import ConfigStore
from minty_core.voice.extras import VoiceExtras
from minty_core.voice.speaker import SpeakerVerifier


def test_the_switches_that_change_how_it_sounds_or_listens_are_off_by_default(tmp_path):
    v = ConfigStore(tmp_path).load().voice
    assert (v.vocabulary, v.whisper, v.ambient_volume, v.pace_matching, v.time_of_day, v.duck_music,
            v.progress_narration, v.bilingual, v.speaker_check) == (False,) * 9
    assert v.corrections and v.playback_controls and v.dictation and v.pronunciations  # phrase-driven only


async def test_a_correction_re_runs_in_one_turn_and_is_stored_once(tmp_path):
    p, f = build(tmp_path, stt_texts=["play tom he who", "no I said play Tum Hi Ho"],
                 replies=[["Playing it."], ["Playing Tum Hi Ho."]])
    f.agent.history = []
    data = p.store.config.model_dump(mode="json")
    data["voice"]["vocabulary"] = True
    p.store.update(data)
    p.voice = VoiceExtras(p.store, tmp_path)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Playing it." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Playing Tum Hi Ho." in f.player.played)
    await finish(p, f, task)
    assert f.agent.heard == ["play tom he who", "play Tum Hi Ho"]  # one extra turn, the corrected words
    assert p.voice.vocabulary.terms() == ["Tum Hi Ho"]


async def test_dictation_never_executes_a_command_phrase(tmp_path):
    from tests.test_voice_controls import _pipeline

    p, f = _pipeline(tmp_path, ["start dictation", "restart minty", "stop dictation"], [], follow_up=True)
    data = p.store.config.model_dump(mode="json")
    data["audio"]["follow_up"] = True
    p.store.update(data)
    from minty_core.commands import CommandRouter

    p.commands = CommandRouter(f.agent, p.store, restart=True)
    task = asyncio.create_task(p.run())
    f.audio.push(b"WAKE", b"SPEECH", b"END", *([b"x"] * 12), b"SPEECH", b"END", *([b"x"] * 12), b"SPEECH", b"END")
    await wait_until(lambda: p.voice.saved, timeout=4)
    await finish(p, f, task)
    assert p.voice.saved == ["restart minty"]
    assert getattr(p.commands, "pending_restart", False) is False


def test_the_speaker_check_meets_its_targets_on_five_synthetic_voices():
    rng = np.random.default_rng(11)
    voices = [rng.normal(size=96) for _ in range(5)]

    def sample(i):
        return (voices[i] + rng.normal(scale=0.3, size=96)).tobytes()

    v = SpeakerVerifier(extractor=lambda pcm: np.frombuffer(pcm, dtype=np.float64))
    v.enrol([sample(0) for _ in range(5)])
    true_accept = sum(v.verify(sample(0)).ok for _ in range(200)) / 200
    false_accept = sum(v.verify(sample(i)).ok for i in range(1, 5) for _ in range(100)) / 400
    assert true_accept >= 0.95 and false_accept <= 0.02
