import asyncio
from datetime import datetime
import logging
import threading
import time

import numpy as np

from minty_core.audio.types import FRAME_SAMPLES, SAMPLE_RATE, Audio
from minty_core.nightmode import scale_pcm, voice_gain
from minty_core.config import ConfigStore

log = logging.getLogger(__name__)
QUEUE_FRAMES = 100  # 8 s of audio: a busy moment never loses the start of a sentence
STALL_S = 3.0  # no audio at all for this long means the device stopped; it is restarted


def parse_device(value: str | None):
    if not value:
        return None
    return int(value) if value.isdigit() else value


class SoundDeviceInput:
    def __init__(self, store: ConfigStore):
        self._store = store
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_FRAMES)
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.dropped = 0  # frames thrown away because nobody read them in time
        self.overflows = 0  # frames the sound system itself lost (reported by PortAudio)
        self.restarts = 0
        self._warned_at = 0.0

    def _offer(self, frame: bytes) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped += 1
            now = time.monotonic()
            if now - self._warned_at > 10:
                self._warned_at = now
                log.warning("microphone audio is not being read fast enough; %d frames dropped so far", self.dropped)
        self._queue.put_nowait(frame)

    async def start(self) -> None:
        import sounddevice as sd

        # Stop existing stream if already running
        if self._stream is not None:
            await self.stop()

        self._loop = asyncio.get_running_loop()

        # Drain queue leftovers from previous session
        while not self._queue.empty():
            self._queue.get_nowait()

        def callback(indata, frames, time_info, status):
            if status and getattr(status, "input_overflow", False):
                self.overflows += 1
            self._loop.call_soon_threadsafe(self._offer, bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            device=parse_device(self._store.config.audio.input_device),
            callback=callback,
        )
        self._stream.start()

    async def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def read(self) -> bytes:
        """The next 80 ms frame. If the device goes silent (unplugged, suspended, PipeWire restarted), it is
        reopened instead of waiting forever."""
        while True:
            try:
                return await asyncio.wait_for(self._queue.get(), STALL_S)
            except asyncio.TimeoutError:
                if self._stream is None:
                    continue
                self.restarts += 1
                log.warning("microphone delivered no audio for %.0f s; reopening it (restart %d)",
                            STALL_S, self.restarts)
                try:
                    await self.restart()
                except Exception as e:  # noqa: BLE001 — device gone: keep trying, never crash the assistant
                    log.warning("could not reopen the microphone: %s", e)
                    await asyncio.sleep(2.0)


def _load_oww(wake_word: str):
    from openwakeword.model import Model

    return Model(wakeword_models=[wake_word], inference_framework="onnx")


class OpenWakeWordDetector:
    def __init__(self, wake_word: str, threshold: float, model_factory=None, boost=lambda: 0.0):
        self._model = (model_factory or _load_oww)(wake_word)
        self._threshold = threshold
        self._boost = boost  # added to the threshold at night (nightmode.wake_boost)

    def detect(self, frame: bytes) -> bool:
        scores = self._model.predict(np.frombuffer(frame, dtype=np.int16))
        return max(scores.values(), default=0.0) >= min(0.99, self._threshold + self._boost())

    def reset(self) -> None:
        reset = getattr(self._model, "reset", None)
        if reset:
            reset()


class _Playback:
    """One clip on its own output stream.

    PortAudio aborts the whole process when a stream is closed from two threads at once, which is what
    sd.play()/sd.wait()/sd.stop() did on a barge-in (a worker thread closed in sd.wait while the loop closed in
    sd.stop). Here the stream is opened, aborted and closed only by `close()`, on the event-loop thread, once.
    The PortAudio thread only feeds samples and sets a flag.
    """

    def __init__(self, sd, audio: Audio, device, loop: asyncio.AbstractEventLoop):
        self._sd, self._pcm, self._pos, self._loop = sd, audio.pcm, 0, loop
        self._finished = asyncio.Event()
        self._closed = False
        self._stream = sd.RawOutputStream(
            samplerate=audio.rate, channels=1, dtype="int16", device=device,
            callback=self._feed, finished_callback=self._on_finished)

    def _feed(self, outdata, frames, time_info, status) -> None:  # PortAudio thread
        need = frames * 2
        chunk = self._pcm[self._pos:self._pos + need]
        self._pos += len(chunk)
        outdata[:len(chunk)] = chunk
        if len(chunk) < need:
            outdata[len(chunk):need] = bytes(need - len(chunk))
            raise self._sd.CallbackStop

    def _on_finished(self) -> None:  # PortAudio thread: no stream calls allowed here
        try:
            self._loop.call_soon_threadsafe(self._finished.set)
        except RuntimeError:  # the loop is already closed (shutdown)
            pass

    def start(self) -> None:
        self._stream.start()

    async def wait(self) -> None:
        await self._finished.wait()

    def close(self) -> None:
        """Idempotent; loop thread only."""
        if self._closed:
            return
        self._closed = True
        for call in (self._stream.abort, self._stream.close):
            try:
                call()
            except Exception as e:  # noqa: BLE001 — a dead device must not take the assistant down
                log.warning("closing the speaker stream failed: %s", e)
        self._finished.set()


class SoundDevicePlayer:
    def __init__(self, store: ConfigStore):
        self._store = store
        self._current: _Playback | None = None

    async def play(self, audio: Audio) -> None:
        import sounddevice as sd

        self.stop()  # one clip at a time
        ops = getattr(self._store.config, "ops", None)
        gain = voice_gain(datetime.now(), ops) if ops is not None else 1.0  # quieter in the quiet hours
        if gain < 1.0:
            audio = Audio(scale_pcm(audio.pcm, gain), audio.rate)
        playback = _Playback(sd, audio, parse_device(self._store.config.audio.output_device),
                             asyncio.get_running_loop())
        self._current = playback
        try:
            playback.start()
            await playback.wait()
        finally:
            playback.close()
            if self._current is playback:
                self._current = None

    def stop(self) -> None:
        if self._current is not None:
            self._current.close()


class StreamingPlayer:
    """Gapless playback for audio that arrives in chunks (live mode). write() never blocks."""

    def __init__(self, store: ConfigStore, rate: int = 24000):
        self._store, self._rate = store, rate
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream = None

    def _callback(self, outdata, frames, time_info, status) -> None:
        need = frames * 2
        with self._lock:
            chunk = bytes(self._buf[:need])
            del self._buf[:need]
        outdata[:] = chunk + bytes(need - len(chunk))

    def write(self, pcm: bytes) -> None:
        if self._stream is None:
            import sounddevice as sd

            self._stream = sd.RawOutputStream(
                samplerate=self._rate, channels=1, dtype="int16", callback=self._callback,
                device=parse_device(self._store.config.audio.output_device))
            self._stream.start()
        with self._lock:
            self._buf.extend(pcm)

    def stop(self) -> None:
        with self._lock:
            self._buf.clear()

    def close(self) -> None:
        self.stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
