"""Notices when the event loop is blocked. Everything voice-related (reading the microphone, state changes, the
display) runs on it, so a blocking call anywhere shows up here as late listening and choppy animation. A helper
thread samples what the loop was running while it was stuck, so the trace says which code froze it."""
import asyncio
import logging
import os
import sys
import threading
import time
import traceback

log = logging.getLogger(__name__)

TICK_S = 0.1
TRACE_MS = 200.0  # a stall this long is written to the trace
WARN_MS = 1000.0  # and this long is also logged
SAMPLE_AFTER_S = 0.15  # the helper thread looks at the loop's stack once it is this late
STACK_FRAMES = 6


def compact_stack(frame, frames: int = STACK_FRAMES, limit: int = 280) -> str:
    """The innermost `frames` frames as `file.py:line function`, outermost first, joined by " > ". When space is short the outer
    frames are dropped, never the inner ones: the frame that was running when the loop froze is the one that matters. (Full paths
    and a cut from the end used to push it out of the trace on a long install path.)"""
    entries = [f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in traceback.extract_stack(frame)[-frames:]]
    kept: list[str] = []
    used = 0
    for entry in reversed(entries):
        cost = len(entry) + (3 if kept else 0)
        if used + cost > limit:
            if not kept:
                kept.append(entry[-limit:])  # even one frame too long: keep its end (the function name)
            break
        kept.append(entry)
        used += cost
    return " > ".join(reversed(kept))


class _Sampler(threading.Thread):
    """Daemon thread: when the loop misses its beat, record the loop thread's stack once."""

    def __init__(self, loop_thread: int, tick: float):
        super().__init__(daemon=True, name="loop-stall-sampler")
        self._loop_thread, self._tick = loop_thread, tick
        self.beat = time.monotonic()
        self.stack = ""
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(0.05):
            if not self.stack and time.monotonic() - self.beat > self._tick + SAMPLE_AFTER_S:
                frame = sys._current_frames().get(self._loop_thread)
                if frame is not None:
                    self.stack = compact_stack(frame)

    def stop(self) -> None:
        self._stop.set()


async def watch(tracer, tick: float = TICK_S) -> None:
    last_warn = 0.0
    sampler = _Sampler(threading.get_ident(), tick)
    sampler.start()
    try:
        while True:
            started = time.perf_counter()
            sampler.beat, sampler.stack = time.monotonic(), ""
            await asyncio.sleep(tick)
            late = (time.perf_counter() - started - tick) * 1000
            if late >= TRACE_MS:
                tracer.event("loop_stall", ms=round(late, 1), stack=sampler.stack)
                now = time.monotonic()
                if late >= WARN_MS and now - last_warn > 30:
                    last_warn = now
                    log.warning("the event loop was blocked for %.0f ms: voice and display froze meanwhile (%s)",
                                late, sampler.stack[-200:])
    finally:
        sampler.stop()
