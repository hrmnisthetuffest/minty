"""What song is this?

1. A music app on this computer (Spotify, a browser tab, ...) reports what it is playing: exact and instant.
   Players that Minty paused while you talk count too.
2. Otherwise, if the computer is making sound, record its own output (no room noise) and ask Shazam.
3. Otherwise, listen through the microphone (music from a phone or speaker) and ask Shazam.
Only steps 2 and 3 send a few seconds of audio to Shazam, and only when you ask.
"""
import asyncio
import io
import logging
import wave

import numpy as np

from minty_core.plugins.host import Tool

log = logging.getLogger(__name__)

RATE = 44100
QUIET_RMS = 60
SHAZAM_TIMEOUT = 25.0
FRIENDLY = {"spotify": "Spotify", "firefox": "your browser", "chromium": "your browser", "chrome": "your browser",
            "vlc": "VLC", "mpv": "mpv"}


async def _run(*args: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, ""
    return proc.returncode, out.decode(errors="replace").strip()


async def now_playing(paused_by_us: list[str]) -> tuple[str, str, str] | None:
    """(title, artist, player) from the first player that is playing, or that Minty paused for the conversation."""
    code, names = await _run("playerctl", "-l")
    if code != 0:
        return None
    for name in names.split():
        code, status = await _run("playerctl", "-p", name, "status")
        if status != "Playing" and name not in paused_by_us:
            continue
        code, meta = await _run("playerctl", "-p", name, "metadata", "--format", "{{title}}\t{{artist}}")
        title, _, artist = meta.partition("\t")
        if code == 0 and title.strip():
            return title.strip(), artist.strip(), name
    return None


async def computer_is_playing() -> bool:
    code, sinks = await _run("pactl", "list", "short", "sinks")
    return any(line.split("\t")[-1:] == ["RUNNING"] for line in sinks.splitlines())


async def record(device: str, seconds: float) -> bytes:
    """Raw 16-bit mono PCM from a PulseAudio/PipeWire source: '@DEFAULT_MONITOR@' (what the computer plays)
    or '@DEFAULT_SOURCE@' (the microphone)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "parec", "-d", device, "--format=s16le", f"--rate={RATE}", "--channels=1", "--raw",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        from minty_core.system.osinfo import detect, hint_for

        raise RuntimeError("parec is missing. " + hint_for(detect(), "pulseaudio-utils")) from None
    chunks, need = [], int(seconds * RATE) * 2
    try:
        while sum(map(len, chunks)) < need:
            chunk = await asyncio.wait_for(proc.stdout.read(65536), seconds + 3)
            if not chunk:
                break
            chunks.append(chunk)
    except asyncio.TimeoutError:
        pass
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    return b"".join(chunks)[:need]


def to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


async def shazam(wav: bytes) -> tuple[str, str] | None:
    from shazamio import Shazam

    result = await asyncio.wait_for(Shazam().recognize(wav), SHAZAM_TIMEOUT)
    track = (result or {}).get("track") or {}
    if not track.get("title"):
        return None
    return str(track["title"]), str(track.get("subtitle") or "")


def _rms(pcm: bytes) -> float:
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0


def register(ctx):
    try:
        seconds = float((ctx.settings or {}).get("listen_seconds") or 8)
        seconds = min(15.0, max(4.0, seconds))
    except (TypeError, ValueError):
        seconds = 8.0
    publish = ctx.services.get("publish", lambda *a, **k: None)

    def paused_by_us() -> list[str]:
        watcher = ctx.services.get("music_watcher")
        return list(getattr(watcher, "paused", []) or [])

    async def identify(args):
        source = str(args.get("source") or "auto").lower()
        if source in ("auto", "app", "apps"):
            playing = await now_playing(paused_by_us())
            if playing:
                title, artist, player = playing
                where = FRIENDLY.get(player.split(".")[0].lower(), player.split(".")[0])
                return f"That's {title}" + (f" by {artist}" if artist else "") + f", playing in {where}."
            if source in ("app", "apps"):
                return "None of your music apps is playing anything right now."
        computer = source == "computer" or (source == "auto" and await computer_is_playing())
        device = "@DEFAULT_MONITOR@" if computer else "@DEFAULT_SOURCE@"
        publish("partial", text="♪ listening to the music…")
        try:
            pcm = await record(device, seconds)
        except RuntimeError as e:
            return f"Error: {e}"
        finally:
            publish("partial", text="")
        if _rms(pcm) < QUIET_RMS:
            return "I can't hear any music right now." if not computer else "The computer is too quiet to tell."
        try:
            found = await shazam(to_wav(pcm))
        except asyncio.TimeoutError:
            return "Error: the song-recognition service did not answer in time."
        except Exception as e:  # network trouble or the unofficial service changed
            log.warning("song recognition failed: %s", e)
            return "Error: song recognition is unavailable right now."
        if found is None:
            return ("I couldn't recognise it. " +
                    ("Try again during a part with vocals." if computer else "Try again closer to the speaker."))
        title, artist = found
        return f"That's {title}" + (f" by {artist}" if artist else "") + "."

    return [
        Tool("identify", "Name the song that is playing ('what song is this', 'what's playing'). Checks music apps "
                         "first, then listens a few seconds.",
             {"type": "object", "properties": {"source": {
                 "type": "string", "description": "auto (default), app, computer (the PC's own sound) or room"}}},
             identify),
    ]
