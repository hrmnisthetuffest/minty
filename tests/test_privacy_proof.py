"""Always-on privacy proof: no audio persisted, and a daily account of what left the machine (W1B-08)."""
import asyncio
import re
import tempfile
from pathlib import Path

import httpx

from tests.test_pipeline import build, finish, wait_until
from minty_core.commands import CommandRouter
from minty_core.destinations import DestinationMap, hooks, plugin_scope, set_registry

SRC = Path(__file__).resolve().parents[1] / "src" / "minty_core"
AUDIO_NAME = re.compile(r"\.(wav|flac|ogg|oga|opus|mp3|m4a|aac|pcm|raw|aiff?)$", re.I)
AUDIO_MAGIC = (b"RIFF", b"fLaC", b"OggS", b"ID3", b"\xff\xfb", b"FORM")


def audio_files(*folders: Path) -> list[Path]:
    found = []
    for folder in folders:
        for p in folder.rglob("*") if folder.exists() else []:
            if p.is_file():
                try:
                    head = p.read_bytes()[:4]
                except OSError:
                    continue
                if AUDIO_NAME.search(p.name) or head in AUDIO_MAGIC:
                    found.append(p)
    return found


async def test_a_long_microphone_feed_leaves_no_audio_file_anywhere(tmp_path, monkeypatch):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    p, f = build(tmp_path / "cfg", stt_texts=["what time is it"] * 3, replies=[["It is noon."]] * 3)
    for _ in range(4000):  # ~5 minutes of 80 ms frames of room noise
        f.audio.push(b"room noise")
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    for _ in range(2000):
        f.audio.push(b"room noise")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"It is noon." in f.player.played)
    await finish(p, f, task)
    assert audio_files(tmp_path / "cfg", scratch) == []


def test_no_runtime_module_writes_audio_to_a_file():
    """Static: the only audio 'writer' is wav.pcm_to_wav, into memory; nothing opens a file for audio."""
    wav = (SRC / "audio" / "wav.py").read_text()
    assert "io.BytesIO" in wav and "wave.open(buf" in wav
    suspicious = []
    for path in list((SRC / "audio").glob("*.py")) + [SRC / "pipeline.py", SRC / "live.py",
                                                      SRC / "plugins" / "builtin" / "music_id" / "plugin.py"]:
        if path.name == "wav.py":
            continue  # checked above: it writes into a BytesIO only
        text = path.read_text()
        for pattern in (r"wave\.open\((?!buf)", r"soundfile", r"sf\.write", r"NamedTemporaryFile", r"mkstemp",
                        r"(?<!wave\.)open\([^)]*['\"]wb['\"]", r"\.write_bytes\("):
            if re.search(pattern, text):
                suspicious.append(f"{path.name}: {pattern}")
    assert suspicious == []


async def test_what_left_the_machine_today_is_counted_per_place_with_sizes(tmp_path):
    clock = {"t": 1_790_000_000.0}
    m = DestinationMap(tmp_path / "d.sqlite", lambda: clock["t"])
    set_registry(m)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                                     event_hooks=hooks()) as c:
            with plugin_scope("weather"):
                await c.post("https://api.weather.example/v1", content=b"x" * 2048)
                await c.get("https://api.weather.example/v1")
            await c.post("https://api.groq.com/openai/v1/chat/completions", content=b"y" * 3 * 1024 * 1024)
    finally:
        set_registry(None)
    text = m.today_summary()
    assert text.startswith("Today I sent 3.0 MB to 2 places.")
    assert "api.groq.com 1 request from core, 3.0 MB" in text and "api.weather.example 2 requests from weather, 2 KB" in text
    clock["t"] += 2 * 86400
    assert m.today_summary() == "Nothing has left this computer today."
    router = CommandRouter(agent=None, destinations=lambda who: m.today_summary() if who == "__today__" else "x")
    assert router.handle("what left the machine today") == "Nothing has left this computer today."
    assert asyncio.get_running_loop() is not None
