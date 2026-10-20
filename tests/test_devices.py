import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from minty_core.audio.devices import OpenWakeWordDetector, SoundDeviceInput, parse_device


def test_parse_device():
    assert parse_device(None) is None
    assert parse_device("") is None
    assert parse_device("3") == 3
    assert parse_device("pulse") == "pulse"


class FakeModel:
    def __init__(self, scores):
        self.scores, self.resets = scores, 0

    def predict(self, samples):
        assert samples.dtype == np.int16 and samples.size == 1280
        return self.scores

    def reset(self):
        self.resets += 1


def test_wake_threshold_and_reset():
    frame = np.zeros(1280, dtype=np.int16).tobytes()
    model = FakeModel({"hey_jarvis_v0.1": 0.7})
    det = OpenWakeWordDetector("hey_jarvis", 0.5, model_factory=lambda name: model)
    assert det.detect(frame) is True
    model.scores = {"hey_jarvis_v0.1": 0.2}
    assert det.detect(frame) is False
    det.reset()
    assert model.resets == 1


async def test_input_queue_drops_oldest_when_full():
    inp = SoundDeviceInput(store=None)
    inp._queue = asyncio.Queue(maxsize=50)
    for i in range(60):
        inp._offer(bytes([i]))
    assert inp._queue.qsize() == 50
    assert await inp.read() == bytes([10])


async def test_restart_wakes_pending_reader():
    """Test that restart() doesn't strand pending read() calls on old queue."""

    # Create fake sounddevice module
    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = None
            self.started = False

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

        def close(self):
            pass

    def make_raw_input_stream(**kwargs):
        stream = FakeStream()
        # Capture the callback
        stream.callback = kwargs.get('callback')
        return stream

    fake_sd = SimpleNamespace(RawInputStream=make_raw_input_stream)

    # Inject fake sounddevice module
    old_sd = sys.modules.get('sounddevice')
    sys.modules['sounddevice'] = fake_sd

    try:
        # Create input with minimal store
        store = SimpleNamespace(config=SimpleNamespace(audio=SimpleNamespace(input_device=None)))
        inp = SoundDeviceInput(store)

        # Start and create a task waiting for read
        await inp.start()
        read_task = asyncio.create_task(inp.read())
        await asyncio.sleep(0.01)  # Let task start waiting

        # Verify read_task is pending
        assert not read_task.done()
        first_stream = inp._stream

        # Restart (this should not break the pending read)
        await inp.restart()
        second_stream = inp._stream

        # Streams should be different
        assert first_stream is not second_stream

        # Invoke NEW stream's callback with data
        second_stream.callback(b"abc", 1280, None, None)

        # The pending read should now complete with the new data
        result = await asyncio.wait_for(read_task, timeout=1.0)
        assert result == b"abc"
    finally:
        # Restore original sounddevice
        if old_sd is None:
            sys.modules.pop('sounddevice', None)
        else:
            sys.modules['sounddevice'] = old_sd
