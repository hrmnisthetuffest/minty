"""While Minty listens, everything else that is playing is turned down a lot (and back up afterwards), so the music is not heard as speech."""
import asyncio

import pytest

from minty_core.config import AudioConfig
from tests.test_pipeline import build, finish, wait_until


class FakeDucker:
    def __init__(self):
        self.level = 0.3
        self.events = []

    def begin(self):
        self.events.append(("duck", self.level))
        return True

    def restore(self):
        self.events.append(("restore",))


class FakeMusic:
    def __init__(self):
        self.paused, self.events = [], []

    def pause(self):
        self.paused = ["player"]
        self.events.append("pause")

    def resume(self):
        self.paused = []
        self.events.append("resume")

    def is_playing(self):
        return True


async def test_the_button_turns_everything_else_down_and_back_up(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["It is noon."]])
    p.listen_ducker = FakeDucker()
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.02)
    p.listen_now()
    f.audio.push(b"x", b"SPEECH", b"END")
    await wait_until(lambda: f.player.played == [b"It is noon."])
    await wait_until(lambda: ("restore",) in p.listen_ducker.events)
    await finish(p, f, task)
    assert p.listen_ducker.events[:2] == [("duck", 0.1), ("restore",)]  # a lot: 10 % of what it was


async def test_saying_the_name_does_it_too(tmp_path):
    p, f = build(tmp_path, stt_texts=["hello"], replies=[["Hi."]])
    p.listen_ducker = FakeDucker()
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: ("restore",) in p.listen_ducker.events)
    await finish(p, f, task)
    assert p.listen_ducker.events[0] == ("duck", 0.1)


async def test_the_level_is_the_one_in_the_settings(tmp_path):
    p, f = build(tmp_path, stt_texts=["hello"], replies=[["Hi."]])
    data = p.store.config.model_dump(mode="json")
    data["audio"]["listen_duck_level"] = 0.25
    p.store.update(data)
    p.listen_ducker = FakeDucker()
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: ("restore",) in p.listen_ducker.events)
    await finish(p, f, task)
    assert p.listen_ducker.events[0] == ("duck", 0.25)


async def test_it_can_be_switched_off(tmp_path):
    p, f = build(tmp_path, stt_texts=["hello"], replies=[["Hi."]])
    data = p.store.config.model_dump(mode="json")
    data["audio"]["listen_duck"] = False
    p.store.update(data)
    p.listen_ducker = FakeDucker()
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.player.played == [b"Hi."])
    await finish(p, f, task)
    assert [e for e in p.listen_ducker.events if e[0] == "duck"] == []


async def test_the_music_is_turned_down_before_it_is_paused_and_both_are_undone(tmp_path):
    p, f = build(tmp_path, stt_texts=["hello"], replies=[["Hi."]])
    log = []
    ducker = FakeDucker()
    ducker.begin = lambda: log.append("duck") or True
    ducker.restore = lambda: log.append("restore")
    music = FakeMusic()
    music.pause = lambda: log.append("pause") or setattr(music, "paused", ["player"])
    music.resume = lambda: log.append("resume") or setattr(music, "paused", [])
    p.listen_ducker, p.music = ducker, music
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: "restore" in log)
    await finish(p, f, task)
    assert log[0] == "duck" and set(log) == {"duck", "pause", "resume", "restore"}
    assert log.index("restore") > log.index("resume")  # volume comes back after the song is playing again


def test_the_defaults_are_a_lot_quieter_and_on():
    cfg = AudioConfig()
    assert cfg.listen_duck is True and cfg.listen_duck_level == 0.1


SINK_INPUTS = """Sink Input #31
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.process.id = "900"
Sink Input #44
\tVolume: front-left: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.process.id = "4242"
"""


def _runner(calls, inputs=SINK_INPUTS):
    def run(argv):
        calls.append(argv)
        return (0, inputs) if argv[:3] == ["pactl", "list", "sink-inputs"] else (0, "")
    return run


def test_a_killed_run_cannot_leave_the_other_apps_quiet(tmp_path):
    from minty_core.voice.adapt import Ducker
    state = tmp_path / "ducked.json"
    first_calls = []
    d = Ducker(_runner(first_calls), own_pid=4242, level=0.1, state_path=state)
    assert d.begin() is True and state.exists()  # what it turned down is written down
    # the process is killed here: no restore() ever runs. The next start puts the volume back.
    later = []
    Ducker(_runner(later), own_pid=4242, state_path=state).recover()
    assert ["pactl", "set-sink-input-volume", "31", "100%"] in later and not state.exists()


def test_a_normal_restore_removes_the_note(tmp_path):
    from minty_core.voice.adapt import Ducker
    state = tmp_path / "ducked.json"
    d = Ducker(_runner([]), own_pid=4242, level=0.1, state_path=state)
    d.begin()
    d.restore()
    assert not state.exists()
    d.recover()  # nothing to do, no error


def test_recover_ignores_a_damaged_or_missing_note(tmp_path):
    from minty_core.voice.adapt import Ducker
    state = tmp_path / "ducked.json"
    state.write_text("{not json")
    calls = []
    Ducker(_runner(calls), state_path=state).recover()
    Ducker(_runner(calls), state_path=tmp_path / "nothing.json").recover()
    assert not any(c[:2] == ["pactl", "set-sink-input-volume"] for c in calls)


async def test_whatever_happens_the_volume_is_put_back_when_the_pipeline_stops(tmp_path):
    p, f = build(tmp_path, stt_texts=[], replies=[])
    p.listen_ducker = FakeDucker()
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.02)
    await finish(p, f, task)
    assert ("restore",) in p.listen_ducker.events


async def test_the_sound_comes_back_even_if_the_turn_is_cancelled_while_saying_yes_sir(tmp_path):
    p, f = build(tmp_path, stt_texts=[], replies=[], wake_ack="Yes, sir?")
    log = []
    music = FakeMusic()
    music.pause = lambda: log.append("pause") or setattr(music, "paused", ["player"])
    music.resume = lambda: log.append("resume") or setattr(music, "paused", [])
    p.music = music
    entered = asyncio.Event()

    async def stuck_ack():
        entered.set()
        await asyncio.sleep(60)

    p._acknowledge = stuck_ack
    task = asyncio.create_task(p.run())
    f.audio.push(b"WAKE")
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert log == ["pause", "resume"]  # the paused song is resumed, not left silent
