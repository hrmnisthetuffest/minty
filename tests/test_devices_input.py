import asyncio

from minty_core.audio import devices
from minty_core.audio.devices import SoundDeviceInput


class Store:
    config = None


async def test_frames_are_counted_when_dropped_and_the_newest_kept(monkeypatch):
    monkeypatch.setattr(devices, "QUEUE_FRAMES", 3)
    mic = SoundDeviceInput(Store())
    for i in range(5):
        mic._offer(bytes([i]))
    assert mic.dropped == 2
    assert [await mic.read() for _ in range(3)] == [bytes([2]), bytes([3]), bytes([4])]


async def test_a_silent_device_is_reopened_instead_of_waiting_forever(monkeypatch):
    monkeypatch.setattr(devices, "STALL_S", 0.05)
    mic = SoundDeviceInput(Store())
    mic._stream = object()  # "running", but delivering nothing

    async def restart():
        mic._offer(b"fresh")

    mic.restart = restart
    assert await asyncio.wait_for(mic.read(), 1) == b"fresh" and mic.restarts == 1


async def test_a_device_that_cannot_be_reopened_is_retried_without_crashing(monkeypatch):
    monkeypatch.setattr(devices, "STALL_S", 0.02)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(devices.asyncio, "sleep", lambda s: real_sleep(0.01))
    mic = SoundDeviceInput(Store())
    mic._stream = object()
    tries = []

    async def restart():
        tries.append(1)
        if len(tries) < 3:
            raise OSError("device unplugged")
        mic._offer(b"back")

    mic.restart = restart
    assert await asyncio.wait_for(mic.read(), 2) == b"back" and len(tries) == 3
