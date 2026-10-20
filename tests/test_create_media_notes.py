"""209 + 667 media tools (real ffmpeg), 666 + 772 notes from transcripts and audio, and source tracking."""
import hashlib
import json
import os
import shutil
import subprocess
import wave

import pytest

from minty_core.create.db import CreateDB
from minty_core.create.media import Media, make_runner, parse_time
from minty_core.create.notes import build_notes, transcribe_file
from minty_core.create.sources import Sources

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="no ffmpeg")


def tone(path, seconds, hz=440):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={hz}:duration={seconds}", "-ac", "1", "-ar", "16000", str(path)], check=True)
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("text,seconds", [("90", 90), ("90s", 90), ("1:20", 80), ("1:02:03", 3723), ("1h2m3s", 3723),
                                          ("1m30s", 90), ("2.5", 2.5), ("0:05", 5)])
def test_times_are_understood(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("bad", ["", "-5", "abc", "1:2:3:4", "1e9", "99999999"])
def test_odd_times_are_refused(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


@needs_ffmpeg
async def test_trim_keeps_the_original_untouched_and_writes_a_new_shorter_file(tmp_path):
    (tmp_path / "media").mkdir()
    src = tone(tmp_path / "media" / "talk.wav", 6)
    before = sha(src)
    m = Media(make_runner(), [tmp_path / "media"], tmp_path / "out")
    info = await m.info(src)
    assert 5.9 < info["duration"] < 6.1 and info["kind"] == "audio"
    out = await m.trim(src, 1, 3)
    assert out.parent == tmp_path / "out" and out != src and 1.9 < (await m.info(out))["duration"] < 2.1
    assert sha(src) == before


@needs_ffmpeg
async def test_output_in_the_same_folder_still_never_overwrites_the_original(tmp_path):
    (tmp_path / "media").mkdir()
    src = tone(tmp_path / "media" / "talk.wav", 4)
    before = sha(src)
    m = Media(make_runner(), [tmp_path / "media"], tmp_path / "media")  # outputs land beside the originals
    a = await m.trim(src, 0, 1, name="talk")  # asks for the original's own name
    b = await m.trim(src, 0, 1, name="talk")
    assert a.name == "talk-1.wav" and b.name == "talk-2.wav" and sha(src) == before


@needs_ffmpeg
async def test_merge_joins_clips_in_order_and_cleans_up_its_list(tmp_path):
    (tmp_path / "media").mkdir()
    a, b = tone(tmp_path / "media" / "a.wav", 2), tone(tmp_path / "media" / "b.wav", 3, 880)
    m = Media(make_runner(), [tmp_path / "media"], tmp_path / "out")
    out = await m.merge([a, b])
    assert 4.9 < (await m.info(out))["duration"] < 5.1
    assert not [p for p in (tmp_path / "out").iterdir() if p.name.startswith(".concat")]
    assert sha(a) and a.exists() and b.exists()


@needs_ffmpeg
async def test_files_outside_the_media_folders_and_odd_inputs_are_refused(tmp_path):
    (tmp_path / "media").mkdir()
    inside = tone(tmp_path / "media" / "ok.wav", 2)
    outside = tone(tmp_path / "elsewhere.wav", 2)
    os.symlink(outside, tmp_path / "media" / "link.wav")
    m = Media(make_runner(), [tmp_path / "media"], tmp_path / "out")
    for bad in (outside, tmp_path / "media" / "link.wav", tmp_path / "media" / "missing.wav", "/etc/passwd"):
        with pytest.raises(ValueError):
            await m.trim(bad, 0, 1)
    (tmp_path / "media" / "notes.txt").write_text("x")
    with pytest.raises(ValueError, match="audio or video"):
        await m.trim(tmp_path / "media" / "notes.txt", 0, 1)
    with pytest.raises(ValueError, match="after the start"):
        await m.trim(inside, 2, 1)
    with pytest.raises(ValueError, match="at least two"):
        await m.merge([inside])


async def test_the_ffmpeg_rules_only_accept_the_three_fixed_shapes():
    r = make_runner()
    base = ["ffmpeg", "-nostdin", "-n", "-hide_banner", "-loglevel", "error"]
    bad = [base + ["-i", "/a.wav", "/b.wav"], base + ["-ss", "1", "-to", "2", "-i", "/a.wav", "-vf", "x", "/b.wav"],
           base + ["-ss", "1", "-to", "2", "-i", "-evil", "-c", "copy", "/b.wav"],
           base + ["-ss", "1; rm", "-to", "2", "-i", "/a.wav", "-c", "copy", "/b.wav"],
           ["ffmpeg", "-y", "-i", "/a.wav", "/b.wav"], ["ffprobe", "-show_streams", "/a.wav"],
           base + ["-f", "concat", "-safe", "0", "-i", "/l.txt", "-c", "copy", "/b.wav", "-map", "0"]]
    for argv in bad:
        with pytest.raises(ValueError):
            await r.run(argv)
    good = [base + ["-ss", "1", "-to", "2.5", "-i", "/a.wav", "-c", "copy", "/b.wav"],
            base + ["-f", "concat", "-safe", "0", "-i", "/l.txt", "-c", "copy", "/b.wav"],
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", "/a.wav"]]
    for argv in good:
        make_runner()._check(argv)  # allowed by the rules (nothing is run)


# ---- notes ---------------------------------------------------------------------------------------------------------
LECTURE = [
    (0.0, "Welcome to the lecture on photosynthesis. Plants make food from light. See https://example.edu/biology for the slides."),
    (300.0, "Chlorophyll absorbs red and blue light and reflects green. That is why leaves look green to us. "
            "The light reactions happen in the thylakoid membranes."),
    (600.0, "The Calvin cycle uses the energy to build sugar from carbon dioxide. Remember that oxygen is a by-product, "
            "not the goal. Questions are welcome at the end."),
]


def test_notes_have_a_summary_chapters_with_times_quotes_links_and_a_cited_source():
    n = build_notes("Photosynthesis lecture", LECTURE, source="lecture.mp3", sha="ab" * 32, duration=900,
                    when="2026-10-18")
    md = n.markdown
    assert md.startswith("# Show notes: Photosynthesis lecture")
    assert "Source: lecture.mp3" in md and "abababab" in md and "15:00" in md  # file, hash and length
    assert "## Chapters" in md and "[00:00]" in md and "[05:00]" in md and "[10:00]" in md
    assert "## Summary" in md and "## Quotes" in md
    assert "https://example.edu/biology" in n.links
    for q in n.quotes:  # quotes are verbatim, never invented
        assert q.text in " ".join(t for _, t in LECTURE)
    assert len(n.chapters) == 3 and all(c.title for c in n.chapters)


def test_a_transcript_without_times_still_gets_numbered_parts():
    n = build_notes("Podcast", [(None, "First part of the talk. " * 30), (None, "Second part with other words. " * 30)],
                    source="pasted text", sha="", duration=None, when="2026-10-18")
    assert "[Part 1]" in n.markdown and "[Part 2]" in n.markdown and "Source: pasted text" in n.markdown


def test_empty_transcripts_are_refused_and_markup_is_not_executed():
    with pytest.raises(ValueError):
        build_notes("x", [(0.0, "   ")], source="s", sha="", duration=None, when="d")
    n = build_notes("<script>alert(1)</script>", [(0.0, "Hello there. This is a talk about things. " * 5)], source="s", sha="",
                    duration=60, when="d")
    assert "<script>" not in n.markdown and "alert(1)" in n.markdown  # shown as text, with the angle brackets gone


@needs_ffmpeg
async def test_an_audio_file_is_transcribed_in_chunks_with_start_times(tmp_path):
    (tmp_path / "media").mkdir()
    src = tone(tmp_path / "media" / "long.wav", 7)
    m = Media(make_runner(), [tmp_path / "media"], tmp_path / "out")
    heard = []

    async def transcribe(pcm, rate):
        heard.append((len(pcm), rate))
        return f"chunk number {len(heard)}"

    segs = await transcribe_file(src, m, transcribe, chunk_s=3)
    assert [round(s) for s, _ in segs] == [0, 3, 6] and [t for _, t in segs] == ["chunk number 1", "chunk number 2", "chunk number 3"]
    assert all(rate == 16000 for _, rate in heard) and heard[0][0] > 90000  # about three seconds of 16-bit mono
    assert not [p for p in (tmp_path / "out").rglob("*") if p.name.endswith(".wav")]  # no chunk files left behind


# ---- sources -------------------------------------------------------------------------------------------------------
def test_a_note_whose_source_is_removed_or_changed_is_flagged(tmp_path):
    db = CreateDB(tmp_path / "c.db")
    s = Sources(db, clock=lambda: 1.0)
    a, b = tmp_path / "lecture.mp3", tmp_path / "other.mp3"
    a.write_bytes(b"audio one")
    b.write_bytes(b"audio two")
    s.record("note-1", "Photosynthesis lecture", "show notes", [(str(a), sha(a))])
    s.record("note-2", "Combined", "summary", [(str(a), sha(a)), (str(b), sha(b))])
    s.record("note-3", "From the web", "summary", [("https://example.edu/page", "")])
    assert s.audit() == []
    b.write_bytes(b"audio two, edited")
    a.unlink()
    flagged = {f["note"]: f["why"] for f in s.audit()}
    assert set(flagged) == {"note-1", "note-2"}
    assert "removed" in flagged["note-1"] and "removed" in flagged["note-2"] and "changed" in flagged["note-2"]
    assert [x["note"] for x in s.list()] == ["note-1", "note-2", "note-3"]
    assert s.list(flagged_only=True)[0]["flagged"]
    s.forget("note-1")
    assert [f["note"] for f in s.audit()] == ["note-2"]
