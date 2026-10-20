"""WP-07 acceptance: every flag defaults off and changes nothing; the stop phrase halts speech quickly; the bench pack
is part of the suite."""
import asyncio
import time

from minty_core.config import ConfigStore
from tests.test_aec import _build as aec_build
from tests.test_pipeline import wait_until


def test_every_new_audio_flag_is_off_by_default(tmp_path):
    a = ConfigStore(tmp_path).load().audio
    assert (a.earcons, a.smart_turn, a.push_to_talk, a.aec, a.hands_free_stop) == ("off", False, False, False, False)


async def test_a_spoken_stop_halts_the_speech_well_inside_half_a_second(tmp_path):
    p, f = aec_build(tmp_path)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Once upon a time." in f.player.played)
    t0 = time.perf_counter()
    f.audio.push(b"STOP")
    await wait_until(lambda: f.player.stopped >= 1)
    elapsed = time.perf_counter() - t0
    f.audio.close()
    await asyncio.wait_for(task, 2)
    assert elapsed <= 0.5, elapsed


def test_the_benchmark_pack_is_in_the_suite():
    from pathlib import Path

    tests = Path(__file__).parent
    assert (tests / "test_voice_bench.py").is_file() and (tests / "test_quiet_speech.py").is_file()
