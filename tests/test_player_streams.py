"""PortAudio aborts the whole process ("PaUnixMutex_Terminate: Assertion `0 == paUtilErr_' failed") when one
stream is closed from two threads at once. The player must open, abort and close each stream on one thread."""
import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from minty_core.audio.devices import SoundDevicePlayer
from minty_core.audio.types import Audio


class CallbackStop(Exception):
    pass


class CallbackAbort(Exception):
    pass


class FakePortAudio:
    """Stands in for the `sounddevice` module. Streams run a real callback thread, like PortAudio does."""

    def __init__(self):
        self.streams = []
        self.fail_open = False
        self.CallbackStop, self.CallbackAbort = CallbackStop, CallbackAbort
        outer = self

        class RawOutputStream:
            def __init__(self, samplerate, channels, dtype, device=None, callback=None, finished_callback=None,
                         **_):
                if outer.fail_open:
                    raise OSError("no output device")
                self.callback, self.finished_callback = callback, finished_callback
                self.played = bytearray()
                self.control = []  # (call, thread id) for start/stop/abort/close
                self.closed = 0
                self._halt = threading.Event()
                self._thread = None
                outer.streams.append(self)

            def _note(self, call):
                self.control.append((call, threading.get_ident()))

            def _run(self):
                try:
                    while not self._halt.is_set():
                        out = bytearray(2 * 480)
                        try:
                            self.callback(out, 480, None, None)
                        except CallbackStop:
                            self.played += out
                            break
                        except CallbackAbort:
                            break
                        self.played += out
                        time.sleep(0.005)
                finally:
                    if self.finished_callback:
                        self.finished_callback()

            def start(self):
                self._note("start")
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

            def _halt_and_join(self):
                self._halt.set()
                if self._thread is not None and self._thread is not threading.current_thread():
                    self._thread.join(2)

            def stop(self, ignore_errors=True):
                self._note("stop")
                self._halt_and_join()

            def abort(self, ignore_errors=True):
                self._note("abort")
                self._halt_and_join()

            def close(self, ignore_errors=True):
                self._note("close")
                self.closed += 1
                self._halt_and_join()

        self.RawOutputStream = RawOutputStream


@pytest.fixture
def pa(monkeypatch):
    fake = FakePortAudio()
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


def player():
    store = SimpleNamespace(config=SimpleNamespace(audio=SimpleNamespace(output_device="")))
    return SoundDevicePlayer(store)


def clip(seconds=0.2, rate=24000):
    return Audio(pcm=bytes(range(256)) * (int(seconds * rate) * 2 // 256), rate=rate)


def only_the_loop_thread_touched(stream):
    return {tid for _, tid in stream.control} == {threading.get_ident()}


async def test_a_clip_plays_to_the_end_and_its_stream_is_closed_exactly_once_on_the_loop_thread(pa):
    audio = clip(0.1)
    await asyncio.wait_for(player().play(audio), 3)
    (stream,) = pa.streams
    assert stream.closed == 1 and only_the_loop_thread_touched(stream)
    assert bytes(stream.played[:len(audio.pcm)]) == audio.pcm


async def test_cancelling_playback_closes_the_stream_once_without_help_from_another_thread(pa):
    p = player()
    task = asyncio.create_task(p.play(clip(5)))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    (stream,) = pa.streams
    assert stream.closed == 1 and only_the_loop_thread_touched(stream)


async def test_barge_in_cancel_then_stop_does_not_close_twice(pa):
    p = player()
    task = asyncio.create_task(p.play(clip(5)))
    await asyncio.sleep(0.05)
    task.cancel()
    p.stop()  # what the pipeline does right after cancelling
    await asyncio.gather(task, return_exceptions=True)
    p.stop()
    (stream,) = pa.streams
    assert stream.closed == 1 and only_the_loop_thread_touched(stream)


async def test_stop_ends_a_running_playback_cleanly(pa):
    p = player()
    task = asyncio.create_task(p.play(clip(5)))
    await asyncio.sleep(0.05)
    p.stop()
    await asyncio.wait_for(task, 2)  # returns, no cancel needed
    (stream,) = pa.streams
    assert stream.closed == 1 and only_the_loop_thread_touched(stream)


async def test_stop_with_nothing_playing_is_harmless(pa):
    p = player()
    p.stop()
    await p.play(clip(0.05))
    p.stop()
    assert [s.closed for s in pa.streams] == [1]


async def test_a_new_clip_replaces_a_running_one_and_each_stream_closes_once(pa):
    p = player()
    first = asyncio.create_task(p.play(clip(5)))
    await asyncio.sleep(0.05)
    await asyncio.wait_for(p.play(clip(0.05)), 3)
    await asyncio.wait_for(first, 2)
    assert [s.closed for s in pa.streams] == [1, 1]
    assert all(only_the_loop_thread_touched(s) for s in pa.streams)


async def test_hammering_cancel_and_stop_never_double_closes(pa):
    p = player()
    for i in range(40):
        task = asyncio.create_task(p.play(clip(1)))
        await asyncio.sleep(0.001 * (i % 7))
        task.cancel()
        p.stop()
        await asyncio.gather(task, return_exceptions=True)
    assert pa.streams and all(s.closed == 1 for s in pa.streams)
    assert all(only_the_loop_thread_touched(s) for s in pa.streams)


async def test_no_output_device_raises_and_leaves_nothing_open(pa):
    pa.fail_open = True
    p = player()
    with pytest.raises(OSError):
        await p.play(clip(0.05))
    p.stop()
