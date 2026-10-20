"""602 lesson/meeting recorder, 744 mark this moment, 755 consent reminder: audio only inside an explicit session."""
import asyncio
import os
import stat
import wave
from pathlib import Path

import pytest

from minty_core.productivity.recorder import REMINDER, Holds, Recorder, RecorderError
from tests.test_pipeline import build, finish, wait_until

RATE = 16000
SECOND = b"\x01\x00" * RATE  # one second of (very quiet) audio


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def rec(tmp_path):
    clock = Clock()
    return Recorder(tmp_path / "rec", clock=clock, chunk_bytes=10 * len(SECOND), max_minutes=2), clock


def files(root: Path):
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def test_nothing_is_written_without_an_explicit_start(rec, tmp_path):
    r, _ = rec
    for _ in range(50):
        r.feed(SECOND)
    assert r.recording is False and files(tmp_path / "rec") == []


def test_a_session_keeps_audio_in_chunks_and_nothing_outside_it(rec, tmp_path):
    r, clock = rec
    reminder = r.start("Physics class")
    assert reminder == REMINDER and "agrees" in reminder.lower() and "REC" in reminder
    for _ in range(25):
        r.feed(SECOND)
        clock.t += 1
    s = r.stop()
    assert r.recording is False and len(s.chunks) == 3
    assert all(c.stat().st_size <= 10 * len(SECOND) + 1024 for c in s.chunks)
    with wave.open(str(s.chunks[0])) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
    assert s.duration_s == pytest.approx(25, abs=1)
    before = len(files(tmp_path / "rec"))
    r.feed(SECOND)  # after stop: nothing more is kept
    assert len(files(tmp_path / "rec")) == before


def test_the_session_folder_and_files_are_owner_only(rec):
    r, _ = rec
    r.start("x")
    r.feed(SECOND)
    s = r.stop()
    assert stat.S_IMODE(os.stat(s.dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(s.chunks[0]).st_mode) == 0o600


def test_a_recording_stops_itself_at_the_time_cap(rec):
    r, clock = rec
    r.start("long one")
    for _ in range(200):
        r.feed(SECOND)
        clock.t += 1
    assert r.recording is False and r.auto_stopped is True


def test_start_and_stop_misuse_is_refused(rec):
    r, _ = rec
    with pytest.raises(RecorderError):
        r.stop()
    with pytest.raises(RecorderError):
        r.mark("x")
    r.start("a")
    with pytest.raises(RecorderError):
        r.start("b")


def test_marks_carry_the_elapsed_time(rec):
    r, clock = rec
    r.start("lecture")
    clock.t += 65
    assert r.mark("important formula") == 65
    clock.t += 30
    r.mark()
    s = r.stop()
    assert s.marks == [(65.0, "important formula"), (95.0, "mark")]


async def test_finish_makes_a_note_with_summary_actions_marks_and_the_consent_note_then_deletes_the_audio(rec, tmp_path):
    r, clock = rec
    r.start("Physics class")
    for _ in range(12):
        r.feed(SECOND)
        clock.t += 1
    r.mark("Snell's law")
    s = r.stop()
    texts = iter(["Today we studied refraction. Light bends when it enters water. The refractive index measures "
                  "the bending. Remember to revise Snell's law before friday.",
                  "What is total internal reflection? It happens when light cannot leave the denser medium."])
    seen = []

    async def transcribe(pcm, rate):
        seen.append((len(pcm), rate))
        return next(texts)

    saved = {}

    def save(title, markdown, tags):
        saved.update(title=title, markdown=markdown, tags=tags)
        return "2026-10-15-abcd"

    out = await r.finish(s, transcribe, save)
    md = saved["markdown"]
    assert saved["title"].startswith("Physics class") and "recording" in saved["tags"]
    assert "## Summary" in md and "## Action items" in md and "revise Snell's law before friday" in md
    assert "## Marked moments" in md and "Snell's law" in md
    assert "## Consent" in md and REMINDER in md and "on this computer" in md
    assert "## Transcript" in md and "refraction" in md
    assert out.note_id == "2026-10-15-abcd" and out.words > 20 and len(seen) == 2 and all(rate == RATE for _, rate in seen)
    assert not s.dir.exists()  # the audio is gone


async def test_keep_audio_keeps_the_chunks(tmp_path):
    clock = Clock()
    r = Recorder(tmp_path / "rec", clock=clock, keep_audio=True)
    r.start("x")
    r.feed(SECOND)
    s = r.stop()

    async def transcribe(pcm, rate):
        return "hello there everyone today we start"

    await r.finish(s, transcribe, lambda *a: "id")
    assert s.chunks[0].exists()


async def test_silence_gives_an_honest_note_and_still_deletes_the_audio(rec):
    r, _ = rec
    r.start("empty")
    r.feed(SECOND)
    s = r.stop()
    saved = {}

    async def transcribe(pcm, rate):
        return "   "

    await r.finish(s, transcribe, lambda t, m, tags: saved.setdefault("md", m) or "id")
    assert "No speech was found" in saved["md"] and not s.dir.exists()


def test_notices_wait_while_recording_unless_urgent(rec):
    r, _ = rec
    h = Holds(r)
    assert h.holds({"type": "notify", "text": "x"}) is False and h.active() is False
    r.start("x")
    assert h.holds({"type": "notify", "text": "x"}) is True
    assert h.holds({"type": "notify", "text": "alarm", "urgent": True}) is False
    assert h.active() is True


# ---- the pipeline hands over frames only inside a session -----------------------------------------------------
async def test_the_pipeline_gives_the_recorder_frames_only_during_a_session(tmp_path):
    p, f = build(tmp_path / "cfg")
    clock = Clock()
    recorder = Recorder(tmp_path / "rec", clock=clock)
    p.recorder = recorder
    f.audio.push(*([b"\x02\x00" * 320] * 20))  # idle: nothing wakes the assistant, nothing may be kept
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.15)
    assert files(tmp_path / "rec") == []
    recorder.start("class")
    f.audio.push(*([b"\x02\x00" * 320] * 20))
    await asyncio.sleep(0.15)
    s = recorder.stop()
    await finish(p, f, task)
    assert len(s.chunks) == 1 and s.chunks[0].stat().st_size > 20 * 640 - 1
    p2, f2 = build(tmp_path / "cfg2")
    muted = Recorder(tmp_path / "rec2", clock=clock)
    p2.recorder = muted
    p2.muted = True
    muted.start("x")
    f2.audio.push(*([b"\x02\x00" * 320] * 10))
    t2 = asyncio.create_task(p2.run())
    await asyncio.sleep(0.1)
    s2 = muted.stop()
    await finish(p2, f2, t2)
    assert sum(c.stat().st_size for c in s2.chunks if c.exists()) <= 44  # a muted microphone records nothing
