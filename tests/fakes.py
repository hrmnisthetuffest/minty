import asyncio

from minty_core.audio.types import Audio


class FakeAudioIn:
    """Finite frame source. close() makes every later read raise EOFError."""

    def __init__(self):
        self.q = asyncio.Queue()

    async def start(self):
        pass

    async def stop(self):
        pass

    def push(self, *frames):
        for f in frames:
            self.q.put_nowait(f)

    def close(self):
        self.q.put_nowait(None)

    async def read(self):
        frame = await self.q.get()
        if frame is None:
            self.q.put_nowait(None)
            raise EOFError
        return frame


class FakeWake:
    def __init__(self):
        self.resets = 0

    def detect(self, frame):
        return frame == b"WAKE"

    def reset(self):
        self.resets += 1


class FakeEndpointer:
    """SPEECH marks speech; END finishes after speech; TIMEOUT finishes with no speech."""

    def __init__(self):
        self.heard_speech = False
        self.feeds = 0

    def reset(self):
        self.heard_speech = False

    def feed(self, frame):
        self.feeds += 1
        if frame == b"SPEECH":
            self.heard_speech = True
        return frame in (b"END", b"TIMEOUT")


class FakeSTT:
    def __init__(self, texts):
        self.texts, self.calls = list(texts), []

    async def transcribe(self, pcm, rate=16000):
        self.calls.append(pcm)
        return self.texts.pop(0)


class FakeTTS:
    def __init__(self, fail_on=()):
        self.fail_on, self.calls = set(fail_on), []

    async def synthesize(self, text):
        self.calls.append(text)
        if text in self.fail_on:
            raise RuntimeError("tts boom")
        return Audio(text.encode(), 16000)


class FakePlayer:
    def __init__(self, delay=0.0):
        self.delay, self.played, self.stopped = delay, [], 0

    async def play(self, audio):
        self.played.append(audio.pcm)
        await asyncio.sleep(self.delay)

    def stop(self):
        self.stopped += 1


class FakeAgent:
    """replies: one entry per respond() call; each is a list of sentences, an async callable
    taking `confirm` and returning bool (yields Done./Cancelled.), or an Exception to raise."""

    def __init__(self, replies):
        self.replies, self.heard, self.confirm, self.closed = list(replies), [], None, 0
        self.tier, self.force_private = "fast", False

    async def respond(self, text, *, sensitive=False):
        self.heard.append(text)
        reply = self.replies.pop(0)
        try:
            if isinstance(reply, Exception):
                raise reply
            for item in reply:
                if callable(item):
                    yield "Done." if await item(self.confirm) else "Cancelled."
                else:
                    yield item
        finally:
            self.closed += 1
