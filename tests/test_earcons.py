"""Earcons (518): short generated tones; 'off' keeps the spoken acknowledgement exactly as it was."""
import asyncio

import numpy as np

from tests.test_pipeline import build, finish, wait_until
from minty_core.voice.earcons import KINDS, tone


def test_each_earcon_is_short_quiet_and_fades_at_both_ends():
    assert set(KINDS) >= {"wake", "done", "error", "approve"}
    for kind in KINDS:
        a = tone(kind)
        samples = np.frombuffer(a.pcm, dtype=np.int16).astype(np.int32)
        assert 0.05 <= len(samples) / a.rate <= 0.5, kind
        assert np.abs(samples).max() <= 0.3 * 32767
        assert abs(samples[0]) < 500 and abs(samples[-1]) < 500
    assert tone("wake").pcm == tone("wake").pcm and tone("wake").pcm != tone("error").pcm


def _setup(tmp_path, earcons):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["It is noon."]], wake_ack="Yes, sir?")
    data = p.store.config.model_dump(mode="json")
    data["audio"]["earcons"] = earcons
    p.store.update(data)
    return p, f


async def test_off_still_speaks_the_acknowledgement(tmp_path):
    p, f = _setup(tmp_path, "off")
    f.audio.push(b"WAKE", *([b"echo"] * 12), b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"It is noon." in f.player.played)
    await finish(p, f, task)
    assert b"Yes, sir?" in f.player.played and tone("wake").pcm not in f.player.played


async def test_chime_plays_a_tone_instead_of_the_words(tmp_path):
    p, f = _setup(tmp_path, "chime")
    f.audio.push(b"WAKE", *([b"echo"] * 12), b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"It is noon." in f.player.played)
    await finish(p, f, task)
    assert tone("wake").pcm in f.player.played and b"Yes, sir?" not in f.player.played
    assert "Yes, sir?" not in f.tts.calls
