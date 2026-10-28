#!/usr/bin/env python3
"""Is this a good vertical video? Prints what is wrong with it, or OK. Exit 0 good, 1 problems, 2 nothing to check.

    python3 check_video.py out/reel.mp4        a file
    python3 check_video.py out                 the newest .mp4 in a folder

Standalone on purpose: it needs only Python and ffmpeg/ffprobe, so a coding agent can run it inside a project without Minty.
Same rules as Minty's own check: 9:16, h264 and aac, 3 to 90 seconds, audible, not black, under 100 MB.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

MIN_S, MAX_S, MIN_WIDTH, MIN_VOLUME, MAX_MB, MIN_BLACK = 3.0, 90.0, 720, -45.0, 100.0, 24.0


def run(*argv):
    done = subprocess.run(list(argv), capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr


def check(path):
    problems = []
    code, out = run("ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path))
    if code != 0:
        return [f"{path.name} is not a media file ffprobe can read"], None
    data = json.loads(out)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or 0)
    size = path.stat().st_size
    if video is None:
        problems.append("there is no video in it")
    else:
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        if abs(w * 16 - h * 9) > h * 0.16:
            problems.append(f"the shape is {w}x{h}, not 9:16 (vertical)")
        if w < MIN_WIDTH:
            problems.append(f"the width is {w}, below {MIN_WIDTH}")
        if video.get("codec_name") != "h264":
            problems.append(f"the video codec is {video.get('codec_name') or 'unknown'}; Instagram wants h264")
    if duration < MIN_S:
        problems.append(f"it is {duration:.1f} s, shorter than {MIN_S:g} s")
    if duration > MAX_S:
        problems.append(f"it is {duration:.1f} s, longer than {MAX_S:g} s")
    if size > MAX_MB * 1e6:
        problems.append(f"the file is {size / 1e6:.0f} MB, over {MAX_MB:g} MB")
    if audio is None:
        problems.append("there is no audio track")
    else:
        if audio.get("codec_name") != "aac":
            problems.append(f"the audio codec is {audio.get('codec_name')}; Instagram wants aac")
        _, text = run("ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-")
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", text)
        if not m or float(m.group(1)) < MIN_VOLUME:
            problems.append(f"the audio is silent or too quiet (mean volume {m.group(1) if m else 'unknown'} dB)")
    if video is not None:
        _, text = run("ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
                      "fps=2,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-", "-f", "null", "-")
        values = [float(v) for v in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", text)]
        if values and max(values) < MIN_BLACK:
            problems.append(f"the picture looks black (brightest frame {max(values):.0f})")
    info = f"{video.get('width') if video else '?'}x{video.get('height') if video else '?'}, {duration:.1f} s, {size / 1e6:.1f} MB"
    return problems, info


def main(argv):
    target = Path(argv[1]) if len(argv) > 1 else Path("out")
    if target.is_dir():
        found = sorted(target.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not found:
            print(f"no video found in {target}/")
            return 2
        target = found[0]
    if not target.is_file():
        print(f"no video found at {target}")
        return 2
    problems, info = check(target)
    if problems:
        print(f"{target.name}: " + "; ".join(problems))
        return 1
    print(f"OK {target.name}: {info}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
