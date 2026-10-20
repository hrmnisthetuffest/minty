"""Clip trim, merge and probe with ffmpeg (209 667). The arguments are built here from three fixed shapes and checked again by the
runner; inputs must be audio or video files inside the media folders (no links out of them); the output is always a NEW file in
the output folder (`-n` plus unique names), so an original is never overwritten. `-c copy` cuts at the nearest key frame on video."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from minty_core.system.runner import Result, Runner

AV_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".opus", ".aac", ".mp4", ".mkv", ".webm", ".mov", ".avi"}
MAX_SECONDS = 24 * 3600
_T = re.compile(r"\d{1,6}(?:\.\d{1,3})?")
_PATH = re.compile(r"/[^\x00-\x1f'\"\;|&$`<>]+")
_BASE = ["ffmpeg", "-nostdin", "-n", "-hide_banner", "-loglevel", "error"]


def parse_time(text: str) -> float:
    s = str(text).strip().lower()
    if not s or s.startswith("-"):
        raise ValueError("Give a time like 90, 1:20 or 1h2m3s.")
    total = None
    if re.fullmatch(r"\d+(?::\d{1,2}){1,2}", s):
        total = 0.0
        for part in s.split(":"):
            total = total * 60 + int(part)
    elif re.fullmatch(r"(?:\d+h)?(?:\d+m)?(?:\d+(?:\.\d+)?s)?", s) and any(c in s for c in "hms"):
        h, m, sec = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", s).groups()
        total = int(h or 0) * 3600 + int(m or 0) * 60 + float(sec or 0)
    elif re.fullmatch(r"\d+(?:\.\d+)?", s):
        total = float(s)
    if total is None or total > MAX_SECONDS:
        raise ValueError("Give a time like 90, 1:20 or 1h2m3s.")
    return total


def _fmt(seconds: float) -> str:
    return f"{seconds:.3f}".rstrip("0").rstrip(".") or "0"


def _ffmpeg_rule(a: list[str]) -> bool:
    if a[:6] != _BASE:
        return False
    rest = a[6:]
    if len(rest) == 9 and rest[0] == "-ss" and rest[2] == "-to" and rest[4] == "-i" and rest[6:8] == ["-c", "copy"]:
        return bool(_T.fullmatch(rest[1]) and _T.fullmatch(rest[3]) and _PATH.fullmatch(rest[5]) and _PATH.fullmatch(rest[8]))
    if len(rest) == 9 and rest[:4] == ["-f", "concat", "-safe", "0"] and rest[4] == "-i" and rest[6:8] == ["-c", "copy"]:
        return bool(_PATH.fullmatch(rest[5]) and _PATH.fullmatch(rest[8]))
    if len(rest) == 13 and rest[0] == "-ss" and rest[2] == "-t" and rest[4] == "-i" and rest[6:12] == ["-ac", "1", "-ar", "16000", "-f", "wav"]:
        return bool(_T.fullmatch(rest[1]) and _T.fullmatch(rest[3]) and _PATH.fullmatch(rest[5]) and _PATH.fullmatch(rest[12]))
    return False


def _ffprobe_rule(a: list[str]) -> bool:
    return len(a) == 8 and a[1:7] == ["-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json"] \
        and _PATH.fullmatch(a[7]) is not None


def make_runner(timeout: float = 600.0) -> Runner:
    return Runner(allowed={"ffmpeg", "ffprobe"}, rules={"ffmpeg": _ffmpeg_rule, "ffprobe": _ffprobe_rule}, timeout=timeout,
                  max_out=20000)


class Media:
    def __init__(self, runner, input_roots: list[Path], out_dir: Path):
        self.r = runner
        self.out_dir = Path(out_dir).expanduser()
        # inputs may come from the media folders or from earlier outputs (so a trimmed clip can be trimmed again)
        self.roots = [Path(p).expanduser().resolve() for p in input_roots] + [self.out_dir.resolve()]

    def _input(self, path) -> Path:
        p = Path(str(path)).expanduser()
        if not p.exists() or p.is_symlink() and not any(Path(os.path.realpath(p)).is_relative_to(r) for r in self.roots):
            raise ValueError("I cannot find that file in the media folders.")
        real = Path(os.path.realpath(p))
        if not any(real.is_relative_to(r) for r in self.roots) or not real.is_file():
            raise ValueError("I only work on files inside the media folders.")
        if real.suffix.lower() not in AV_EXT:
            raise ValueError("That is not an audio or video file I can work on.")
        if not _PATH.fullmatch(str(real)):
            raise ValueError("That file name has characters I will not pass to ffmpeg; rename it first.")
        return real

    def _output(self, name: str | None, ext: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.out_dir, 0o700)
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "clip")).strip("-.")[:60] or "clip"
        for n in range(1000):
            path = self.out_dir / f"{slug}{'' if n == 0 else f'-{n}'}{ext}"
            if not path.exists():
                return path.resolve()
        raise ValueError("Too many files with that name; pick another.")

    def input_path(self, path) -> Path:
        """A file the tools may work on (inside the media folders or an earlier output), or ValueError."""
        return self._input(path)

    async def info(self, path) -> dict:
        p = self._input(path)
        res = await self.r.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json",
                                str(p)])
        if res.missing:
            raise ValueError("ffprobe is not installed (package ffmpeg).")
        try:
            data = json.loads(res.out)
            kinds = {s.get("codec_type") for s in data.get("streams", [])}
            return {"duration": float(data["format"]["duration"]), "kind": "video" if "video" in kinds else "audio",
                    "path": str(p)}
        except (ValueError, KeyError, TypeError):
            raise ValueError("ffprobe could not read that file.") from None

    async def _run(self, argv: list[str], out: Path) -> Path:
        res: Result = await self.r.run(argv)
        if res.missing:
            raise ValueError("ffmpeg is not installed.")
        if not res.ok or not out.exists():
            out.unlink(missing_ok=True)
            raise ValueError("ffmpeg could not make that clip: " + " ".join(res.err.split())[:160])
        os.chmod(out, 0o600)
        return out

    async def trim(self, path, start: float, end: float, name: str | None = None) -> Path:
        src = self._input(path)
        if not (0 <= float(start) < float(end)):
            raise ValueError("The end must be after the start.")
        out = self._output(name or f"{src.stem}-clip", src.suffix.lower())
        return await self._run([*_BASE, "-ss", _fmt(float(start)), "-to", _fmt(float(end)), "-i", str(src), "-c", "copy",
                                str(out)], out)

    async def merge(self, paths: list, name: str | None = None) -> Path:
        files = [self._input(p) for p in paths]
        if len(files) < 2:
            raise ValueError("Merging needs at least two clips.")
        if len({f.suffix.lower() for f in files}) != 1:
            raise ValueError("Merge clips of the same kind (all .mp3, or all .mp4).")
        out = self._output(name or "merged", files[0].suffix.lower())
        listing = self.out_dir / f".concat-{os.getpid()}-{out.stem}.txt"
        listing.write_text("".join("file '" + str(f).replace("'", "'\\''") + "'\n" for f in files), encoding="utf-8")
        try:
            return await self._run([*_BASE, "-f", "concat", "-safe", "0", "-i", str(listing.resolve()), "-c", "copy", str(out)], out)
        finally:
            listing.unlink(missing_ok=True)

    async def chunk_wav(self, path, start: float, seconds: float, out: Path) -> Path:
        """16 kHz mono WAV of one stretch, for speech recognition (a temporary file the caller deletes)."""
        src = self._input(path)
        return await self._run([*_BASE, "-ss", _fmt(start), "-t", _fmt(seconds), "-i", str(src), "-ac", "1", "-ar", "16000",
                                "-f", "wav", str(out)], out)
