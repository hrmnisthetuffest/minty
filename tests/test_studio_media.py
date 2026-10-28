"""The studio's video tools, against the real ffmpeg on tiny generated clips: what a file really contains is the evidence."""
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from minty_core.studio import media
from minty_core.studio.captions import Caption, chunk_text, to_ass

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")


def run(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture
def clips(tmp_path):
    wide = tmp_path / "wide.mp4"  # a 16:9 "gameplay" clip with colour
    run("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4", "-pix_fmt", "yuv420p", str(wide))
    black = tmp_path / "black.mp4"
    run("-f", "lavfi", "-i", "color=c=black:size=720x1280:rate=30:duration=4", "-pix_fmt", "yuv420p", str(black))
    voice = tmp_path / "voice.wav"
    run("-f", "lavfi", "-i", "sine=frequency=300:duration=3", str(voice))
    music = tmp_path / "music.wav"
    run("-f", "lavfi", "-i", "sine=frequency=110:duration=2", str(music))
    return {"wide": wide, "black": black, "voice": voice, "music": music}


async def test_probe_reads_what_is_in_the_file(clips):
    info = await media.probe(clips["wide"])
    assert (info.width, info.height) == (640, 360) and 3.8 < info.duration < 4.3 and info.has_video and not info.has_audio
    assert info.vcodec == "h264"
    wav = await media.probe(clips["voice"])
    assert wav.has_audio and not wav.has_video and 2.9 < wav.duration < 3.2


async def test_probe_of_a_missing_or_broken_file_is_an_error_not_a_guess(tmp_path):
    with pytest.raises(media.MediaError):
        await media.probe(tmp_path / "nope.mp4")
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video at all")
    with pytest.raises(media.MediaError):
        await media.probe(junk)


async def test_compose_makes_a_vertical_video_with_voice_music_and_captions(clips, tmp_path):
    out = tmp_path / "reel.mp4"
    caps = [Caption(0.2, 1.5, "THIS IS A TEST"), Caption(1.6, 3.0, "OF THE CAPTIONS")]
    await media.compose_vertical(out, background=[clips["wide"]], voiceover=clips["voice"], music=clips["music"], captions=caps)
    info = await media.probe(out)
    assert (info.width, info.height) == (1080, 1920) and info.vcodec == "h264" and info.acodec == "aac"
    assert 3.4 < info.duration < 4.0  # the voiceover (3 s) plus a short tail, not the whole 4 s clip
    report = await media.check_video(out)
    assert report.ok, report.problems


async def test_a_short_background_is_looped_to_cover_a_longer_voiceover(clips, tmp_path):
    long_voice = tmp_path / "long.wav"
    run("-f", "lavfi", "-i", "sine=frequency=300:duration=9", str(long_voice))
    out = tmp_path / "long.mp4"
    await media.compose_vertical(out, background=[clips["wide"]], voiceover=long_voice, music=None, captions=[])
    assert 9.2 < (await media.probe(out)).duration < 10.2  # the clip is 4 s: it was looped, not frozen or cut short


async def test_the_start_inside_a_long_background_is_chosen_so_every_video_is_not_the_same_minute(clips, tmp_path):
    seen = []
    for offset in (0.0, 1.0):
        out = tmp_path / f"o{offset}.mp4"
        await media.compose_vertical(out, background=[clips["wide"]], voiceover=clips["voice"], music=None, captions=[],
                                     start_offset=offset)
        seen.append((await media.frame_hash(out, 1.0)))
    assert seen[0] != seen[1]


async def test_the_check_names_every_problem_it_finds(clips, tmp_path):
    report = await media.check_video(clips["wide"])  # 16:9, no audio
    assert not report.ok and any("9:16" in p for p in report.problems) and any("audio" in p.lower() for p in report.problems)
    black = tmp_path / "blackvoice.mp4"
    await media.compose_vertical(black, background=[clips["black"]], voiceover=clips["voice"], music=None, captions=[])
    report = await media.check_video(black)
    assert not report.ok and any("black" in p.lower() for p in report.problems)
    silent = tmp_path / "silent.wav"
    run("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "3", str(silent))
    out = tmp_path / "quiet.mp4"
    await media.compose_vertical(out, background=[clips["wide"]], voiceover=silent, music=None, captions=[])
    assert any("silent" in p.lower() or "quiet" in p.lower() for p in (await media.check_video(out)).problems)


async def test_length_limits_are_part_of_the_check(clips, tmp_path):
    out = tmp_path / "ok.mp4"
    await media.compose_vertical(out, background=[clips["wide"]], voiceover=clips["voice"], music=None, captions=[])
    assert any("longer than" in p for p in (await media.check_video(out, max_s=2)).problems)
    assert any("shorter than" in p for p in (await media.check_video(out, min_s=30)).problems)


async def test_compose_refuses_what_it_cannot_make(clips, tmp_path):
    with pytest.raises(media.MediaError, match="background"):
        await media.compose_vertical(tmp_path / "x.mp4", background=[], voiceover=clips["voice"], music=None, captions=[])
    with pytest.raises(media.MediaError, match="voiceover or"):
        await media.compose_vertical(tmp_path / "x.mp4", background=[clips["wide"]], voiceover=None, music=None, captions=[])
    with pytest.raises(media.MediaError):
        await media.compose_vertical(tmp_path / "x.mp4", background=[tmp_path / "gone.mp4"], voiceover=clips["voice"], music=None, captions=[])
    assert not (tmp_path / "x.mp4").exists()  # no half-made file is left behind


async def test_a_music_bed_is_made_from_nothing_and_is_audible(tmp_path):
    out = tmp_path / "bed.wav"
    await media.music_bed(out, seconds=4, mood="calm")
    info = await media.probe(out)
    assert info.has_audio and 3.8 < info.duration < 4.3
    assert (await media.mean_volume(out)) > -40
    with pytest.raises(media.MediaError):
        await media.music_bed(out, seconds=4, mood="no-such-mood")


def test_text_is_cut_into_short_caption_chunks_timed_by_length():
    chunks = chunk_text("Did you know that a creeper can not climb ladders in Minecraft", 6.0)
    assert all(1 <= len(c.text.split()) <= 4 for c in chunks)
    assert chunks[0].start >= 0.0 and chunks[-1].end <= 6.0 and all(a.end <= b.start + 1e-6 for a, b in zip(chunks, chunks[1:]))
    assert " ".join(c.text for c in chunks).lower().startswith("did you know")
    assert chunk_text("", 5.0) == [] and chunk_text("hi", 0.0) == []


def test_ass_captions_escape_what_would_break_the_file():
    text = to_ass([Caption(0.0, 1.0, "50% {off} \\ now\nline")], 1080, 1920, font="Sans")
    assert "{off}" not in text.split("Dialogue:")[1] and "Dialogue: 0,0:00:00.00,0:00:01.00" in text
    assert "PlayResX: 1080" in text and "Fontname" in text
