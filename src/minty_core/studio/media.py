"""ffmpeg and ffprobe, wrapped so that what a video contains is a measured fact.

`probe` reads a file, `check_video` compares it with what Instagram Reels want (and with plain sense: not black, not silent), and
`compose_vertical` builds a 9:16 video from a background clip, a voiceover, music and captions. Nothing is trusted from the way a file
was made: after any step, `check_video` says what is really there. A half-made file is never left behind.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from minty_core import osutil, shellkit
from minty_core.studio.sniff import sniff  # noqa: F401  (re-exported: media.sniff)
from minty_core.studio.captions import Caption, to_ass

FILE_ONLY = ["-protocol_whitelist", "file"]  # a clip is read as a plain file: no addresses, no pipes, no other schemes
MIN_BLACK = 24.0  # the brightest frame's average luma (16 is black in video range): below this the picture is blank
MOODS = {  # chord tones (Hz, level) and a slow tremolo: a plain generated bed, not a song
    "calm": ([(110.0, 1.0), (164.81, 0.7), (220.0, 0.6), (329.63, 0.25)], 0.3, 0.4),
    "upbeat": ([(130.81, 1.0), (164.81, 0.8), (196.0, 0.7), (261.63, 0.5)], 2.0, 0.6),
    "tense": ([(55.0, 1.0), (58.27, 0.9), (110.0, 0.4)], 0.5, 0.5),
}


class MediaError(Exception):
    """A file is missing or broken, or ffmpeg could not make what was asked."""


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    has_video: bool
    has_audio: bool
    vcodec: str
    acodec: str
    size: int
    fps: float


@dataclass
class CheckReport:
    ok: bool
    problems: list[str] = field(default_factory=list)
    info: MediaInfo | None = None

    def summary(self) -> str:
        if self.ok and self.info:
            i = self.info
            return f"OK: {i.width}x{i.height}, {i.duration:.1f} s, {i.vcodec}/{i.acodec}, {i.size / 1e6:.1f} MB"
        return "Problems: " + "; ".join(self.problems)


async def _run(argv: list[str], timeout: float = 300.0, cwd: str | None = None) -> str:
    result = await shellkit.run(argv, cwd=cwd, timeout_s=timeout, max_chars=12000)
    if result.missing:
        raise MediaError(f"{argv[0]} is not installed (it is part of ffmpeg)")
    if result.timed_out:
        raise MediaError(f"{argv[0]} took longer than {int(timeout)} seconds and was stopped")
    if result.code != 0:
        raise MediaError(f"{argv[0]} failed: {' '.join(result.text.split())[-300:]}")
    return result.text


async def probe(path: Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"{path.name} is not there")
    if sniff(path) is None:  # by its bytes: a playlist, a list of files or a document is not footage, whatever it is called
        raise MediaError(f"{path.name} is not a media file I accept (it looks like text, a playlist or a document)")
    try:
        out = await _run(["ffprobe", "-v", "error", *FILE_ONLY, "-print_format", "json", "-show_format", "-show_streams", str(path)])
    except MediaError as e:
        if "not installed" in str(e) or "longer than" in str(e):
            raise
        raise MediaError(f"{path.name} is not a media file ffprobe can read") from None
    try:
        data = json.loads(out)
    except ValueError:
        raise MediaError(f"{path.name} is not a media file") from None
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise MediaError(f"{path.name} has no video or audio in it")
    try:
        duration = float((data.get("format") or {}).get("duration") or (video or audio).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    num, _, den = str((video or {}).get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if den and float(den) else 0.0
    return MediaInfo(duration, int((video or {}).get("width") or 0), int((video or {}).get("height") or 0), video is not None,
                     audio is not None, str((video or {}).get("codec_name") or ""), str((audio or {}).get("codec_name") or ""),
                     path.stat().st_size, fps)


async def mean_volume(path: Path) -> float | None:
    text = await _run(["ffmpeg", "-hide_banner", "-nostats", *FILE_ONLY, "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", text)
    return float(m.group(1)) if m else None


async def brightest_frame(path: Path) -> float | None:
    """The highest average luma among frames sampled twice a second (16 is black)."""
    text = await _run(["ffmpeg", "-hide_banner", "-nostats", *FILE_ONLY, "-i", str(path), "-an", "-vf",
                       "fps=2,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-", "-f", "null", "-"])
    values = [float(v) for v in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", text)]
    return max(values) if values else None


async def frame_hash(path: Path, at: float) -> str:
    text = await _run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{at:.2f}", *FILE_ONLY, "-i", str(path), "-frames:v", "1", "-f", "md5", "-"])
    m = re.search(r"MD5=([0-9a-f]+)", text)
    if not m:
        raise MediaError("could not read a frame")
    return m.group(1)


async def check_video(path: Path, *, min_s: float = 3.0, max_s: float = 90.0, min_width: int = 720, min_volume: float = -45.0,
                      max_mb: float = 100.0) -> CheckReport:
    """What is really in this file, against what a Reel needs. Every problem is listed, not just the first."""
    try:
        info = await probe(path)
    except MediaError as e:
        return CheckReport(False, [str(e)])
    problems: list[str] = []
    if not info.has_video:
        problems.append("there is no video in it")
    else:
        if abs(info.width * 16 - info.height * 9) > info.height * 0.16:  # within about 1% of 9:16
            problems.append(f"the shape is {info.width}x{info.height}, not 9:16 (vertical)")
        if info.width < min_width:
            problems.append(f"the width is {info.width}, below {min_width}")
        if info.vcodec != "h264":
            problems.append(f"the video codec is {info.vcodec or 'unknown'}; Instagram wants h264")
    if info.duration < min_s:
        problems.append(f"it is {info.duration:.1f} s, shorter than {min_s:g} s")
    if info.duration > max_s:
        problems.append(f"it is {info.duration:.1f} s, longer than {max_s:g} s")
    if info.size > max_mb * 1e6:
        problems.append(f"the file is {info.size / 1e6:.0f} MB, over {max_mb:g} MB")
    if not info.has_audio:
        problems.append("there is no audio track")
    else:
        if info.acodec != "aac":
            problems.append(f"the audio codec is {info.acodec}; Instagram wants aac")
        loud = await mean_volume(path)
        if loud is None or loud < min_volume:
            problems.append(f"the audio is silent or too quiet (mean volume {loud if loud is not None else 'unknown'} dB)")
    if info.has_video:
        bright = await brightest_frame(path)
        if bright is not None and bright < MIN_BLACK:
            problems.append(f"the picture looks black (brightest frame {bright:.0f})")
    return CheckReport(not problems, problems, info)


# ---- font for captions ---------------------------------------------------------------------------------------------------
def filter_path(path) -> str:
    """A folder as ffmpeg's filter syntax wants it: forward slashes, and the colon after a Windows drive letter escaped (`C\\:/Windows/Fonts`)."""
    return Path(path).as_posix().replace(":", "\\:")


async def _font() -> tuple[str, str | None]:
    """(family, folder) of a bold sans font on this computer: from fontconfig on Linux, the system fonts folder on Windows; never a guessed path."""
    if osutil.IS_WINDOWS:
        fonts = Path(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "C:/Windows") / "Fonts"
        return ("Arial", filter_path(fonts)) if (fonts / "arialbd.ttf").is_file() else ("Arial", None)
    try:
        text = await _run(["fc-match", "-f", "%{family}|%{file}", "sans:bold"], timeout=10)
        family, _, file = text.partition("|")
        return family.split(",")[0].strip() or "Sans", str(Path(file.strip()).parent) if file.strip() else None
    except MediaError:
        return "Sans", None


# ---- making a vertical video ---------------------------------------------------------------------------------------------
async def compose_vertical(out: Path, *, background: list[Path], voiceover: Path | None, music: Path | None,
                           captions: list[Caption], seconds: float | None = None, size: tuple[int, int] = (1080, 1920),
                           music_volume: float = 0.12, fps: int = 30, start_offset: float | None = None,
                           timeout: float = 900.0) -> Path:
    """Background clip(s) scaled and cropped to fill the frame (looped if too short), the voiceover over music kept low, captions
    burned in. The length is the voiceover plus a short tail. Written under a temporary name and moved into place when it worked."""
    out = Path(out)
    if not background:
        raise MediaError("give at least one background clip")
    if voiceover is None and not seconds:
        raise MediaError("give a voiceover or a length in seconds")
    infos = [await probe(p) for p in background]
    if not all(i.has_video for i in infos):
        raise MediaError("a background clip has no picture in it")
    vo = await probe(voiceover) if voiceover else None
    total = max(1.0, min((vo.duration + 0.6) if vo else float(seconds), 180.0))
    width, height = size
    per = total / len(background)
    tmp = Path(tempfile.mkdtemp(prefix="studio-"))
    part = out.with_name(out.stem + ".part.mp4")
    try:
        argv, graph = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error"], []
        for i, (p, info) in enumerate(zip(background, infos)):
            offset = start_offset if start_offset is not None else (
                random.uniform(0, info.duration - per - 0.1) if info.duration > per + 0.6 else 0.0)
            argv += ["-stream_loop", "-1", "-ss", f"{max(0.0, offset):.2f}", *FILE_ONLY, "-i", str(p)]
            graph.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,"
                         f"fps={fps},trim=duration={per:.3f},setpts=PTS-STARTPTS[v{i}]")
        n = len(background)
        graph.append("".join(f"[v{i}]" for i in range(n)) + (f"concat=n={n}:v=1:a=0[vc]" if n > 1 else "null[vc]"))
        if captions:
            family, fonts = await _font()
            (tmp / "c.ass").write_text(to_ass(captions, width, height, font=family), encoding="utf-8")
            graph.append(f"[vc]ass=c.ass{':fontsdir=' + fonts if fonts else ''}[vout]")
        else:
            graph.append("[vc]null[vout]")
        stereo = "aformat=sample_rates=44100:channel_layouts=stereo"
        nxt = n
        parts = []
        if voiceover:
            argv += [*FILE_ONLY, "-i", str(voiceover)]
            graph.append(f"[{nxt}:a]apad,atrim=duration={total:.3f},{stereo}[avo]")
            parts.append("[avo]")
            nxt += 1
        if music:
            argv += ["-stream_loop", "-1", *FILE_ONLY, "-i", str(music)]
            fade = max(0.0, total - 1.2)
            graph.append(f"[{nxt}:a]atrim=duration={total:.3f},volume={music_volume if voiceover else 0.6},"
                         f"afade=t=out:st={fade:.3f}:d=1.2,{stereo}[amu]")
            parts.append("[amu]")
            nxt += 1
        if len(parts) == 2:
            graph.append("[avo][amu]amix=inputs=2:duration=first:dropout_transition=0,volume=2,alimiter=limit=0.95[aout]")
        elif parts:
            graph.append(f"{parts[0]}anull[aout]")
        else:
            argv += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo"]
            graph.append(f"[{nxt}:a]atrim=duration={total:.3f}[aout]")
        argv += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(fps),
                 "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", str(part)]
        out.parent.mkdir(parents=True, exist_ok=True)
        await _run(argv, timeout=timeout, cwd=str(tmp))
        part.replace(out)
        return out
    finally:
        part.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


async def music_bed(out: Path, seconds: float, mood: str = "calm") -> Path:
    """A plain generated backing sound (soft chord tones with a slow swell). Royalty-free because it is made here; it is a bed
    under a voice, not a song."""
    if mood not in MOODS:
        raise MediaError(f"mood must be one of: {', '.join(MOODS)}")
    tones, tremolo, depth = MOODS[mood]
    seconds = max(1.0, min(float(seconds), 180.0))
    argv = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error"]
    for freq, _ in tones:
        argv += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds:.2f}"]
    weights = " ".join(f"{level}" for _, level in tones)
    graph = (f"amix=inputs={len(tones)}:weights={weights}:duration=longest,volume={len(tones) * 2},tremolo=f={tremolo}:d={depth},"
             f"lowpass=f=1800,afade=t=in:d=1,afade=t=out:st={max(0.0, seconds - 1.5):.2f}:d=1.5,aformat=channel_layouts=stereo")
    out = Path(out)
    part = out.with_name(out.stem + ".part" + out.suffix)
    try:
        await _run(argv + ["-filter_complex", graph, str(part)])
        part.replace(out)
        return out
    finally:
        part.unlink(missing_ok=True)


async def poster(src: Path, dst: Path, width: int = 360) -> Path:
    """One small JPEG frame of a clip, to show before it plays and in lists, so the clip itself is not fetched to be looked at."""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.stem + ".part.jpg")
    try:
        await _run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-ss", "1", *FILE_ONLY, "-i", str(src), "-frames:v", "1",
                    "-vf", f"scale={int(width)}:-2", "-q:v", "5", "-pix_fmt", "yuvj420p", str(part)])
        if not part.exists():  # a clip shorter than a second has no frame there
            await _run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error", *FILE_ONLY, "-i", str(src), "-frames:v", "1",
                        "-vf", f"scale={int(width)}:-2", "-q:v", "5", "-pix_fmt", "yuvj420p", str(part)])
        part.replace(dst)
        return dst
    finally:
        part.unlink(missing_ok=True)


async def make_background(src: Path, dst: Path, *, max_seconds: float = 60.0, size: tuple[int, int] = (720, 1280), fps: int = 30) -> Path:
    """A downloaded clip as the background it will be used as: portrait, no sound, no longer than needed. A 3-minute 720p download
    becomes a few megabytes, and every video made from it later does less work."""
    src, dst = Path(src), Path(dst)
    info = await probe(src)
    start = min(2.0, info.duration * 0.05) if info.duration > max_seconds + 4 else 0.0  # skip a slate at the very start of a long clip
    width, height = size
    part = dst.with_name(dst.stem + ".part.mp4")
    try:
        await _run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{max_seconds:.2f}", *FILE_ONLY,
                    "-i", str(src), "-an", "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-maxrate", "2500k", "-bufsize", "5000k", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(part)], timeout=600.0)  # a background does not need more than about 2.5 Mbit/s
        part.replace(dst)
        return dst
    finally:
        part.unlink(missing_ok=True)


async def generated_background(out: Path, seconds: float = 20.0, size: tuple[int, int] = (720, 1280)) -> Path:
    """A slow, soft, animated colour background (no third party in it): what a video gets when nothing else is available."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".part.mp4")
    width, height = size
    try:
        await _run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"gradients=s={width}x{height}:d={max(1.0, float(seconds)):.2f}:r=30:speed=0.02:c0=0x1a2a6c:c1=0xb21f1f:c2=0xfdbb2d:nb_colors=3",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(part)])
        part.replace(out)
        return out
    finally:
        part.unlink(missing_ok=True)


async def to_jpeg(src: Path, dst: Path, width: int = 1080) -> Path:
    """A picture as the JPEG Instagram requires (it takes no other format), no wider than `width`."""
    dst = Path(dst)
    part = dst.with_name(dst.stem + ".part" + dst.suffix)
    try:
        await _run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error", *FILE_ONLY, "-i", str(src), "-vf",
                    f"scale='min({width},iw)':-2", "-frames:v", "1", "-q:v", "2", "-pix_fmt", "yuvj420p", str(part)])
        part.replace(dst)
        return dst
    finally:
        part.unlink(missing_ok=True)
