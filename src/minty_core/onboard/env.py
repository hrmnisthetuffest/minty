"""What the setup does to the outside world, in one place so tests can replace every piece.

Nothing here runs unless a step asks, and every step that downloads or installs asks you first and says what and how big.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from minty_core.wizard import mic_level_test

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
VOICE_FILES = {
    "piper": [(f"{PIPER_BASE}/en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx"),
              (f"{PIPER_BASE}/en_US-lessac-medium.onnx.json", "en_US-lessac-medium.onnx.json")],
    "kokoro": [(f"{KOKORO_BASE}/kokoro-v1.0.onnx", "kokoro-v1.0.onnx"),
               (f"{KOKORO_BASE}/voices-v1.0.bin", "voices-v1.0.bin")],
}
VOICE_SIZE = {"piper": "about 65 MB", "kokoro": "about 340 MB"}
# SHA-256 of each file. The two Piper files were checked against Hugging Face's published digests (the .onnx's LFS sha256; the small
# .json's git object id). GitHub publishes no digest for the Kokoro release; those two are pinned from the copies downloaded on
# 2026-10-10, whose sizes match the release listing. A model is loaded into Minty's own process: it must be the one that was pinned.
VOICE_SHA256 = {
    "en_US-lessac-medium.onnx": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
    "en_US-lessac-medium.onnx.json": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "minty"


def checkout_root() -> Path | None:
    """The folder this code runs from when it is a source checkout (it has a pyproject.toml), else None."""
    here = Path(__file__).resolve().parents[3]
    return here if (here / "pyproject.toml").exists() and (here / "src" / "minty_core").is_dir() else None


def run_command(cmd: list[str], *, timeout: int = 1800) -> tuple[bool, str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"
    tail = "\n".join((out.stdout + out.stderr).strip().splitlines()[-6:])
    return out.returncode == 0, tail


def pip_install(spec: str) -> tuple[bool, str]:
    """Install into the interpreter that runs Minty. A source checkout gets an editable install of itself."""
    root = checkout_root()
    if spec.startswith("minty-core[") and root is not None:
        return run_command([sys.executable, "-m", "pip", "install", "-e", f"{root}{spec[len('minty-core'):]}"])
    return run_command([sys.executable, "-m", "pip", "install", spec])


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path) -> tuple[bool, str]:
    """Fetch one file to `dest` (through a .part file, so a broken download never looks finished). A file with a pinned digest
    must match it, whether it is downloaded now or already on disk."""
    import httpx
    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn

    pinned = VOICE_SHA256.get(dest.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        if pinned is None or _file_sha256(dest) == pinned:
            return True, f"{dest.name} is already there."
        dest.unlink()  # not the file that was pinned: fetch it again rather than load it
    part = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            with Progress(TextColumn("  {task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn()) as bar:
                task = bar.add_task(dest.name, total=total or None)
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        digest.update(chunk)
                        bar.update(task, advance=len(chunk))
        if pinned is not None and digest.hexdigest() != pinned:
            part.unlink(missing_ok=True)
            return False, f"{dest.name}: the download does not match its published checksum, so it was thrown away"
        part.replace(dest)
    except (httpx.HTTPError, OSError) as e:
        part.unlink(missing_ok=True)
        return False, f"{dest.name}: {type(e).__name__}"
    return True, f"{dest.name} saved."


@dataclass
class Env:
    directory: Path
    which: Callable[[str], str | None] = shutil.which
    has_module: Callable[[str], bool] = lambda name: importlib.util.find_spec(name) is not None
    python: tuple[int, int, int] = tuple(sys.version_info[:3])  # type: ignore[assignment]
    system: str = field(default_factory=platform.system)
    root: Path | None = field(default_factory=checkout_root)
    data: Path = field(default_factory=data_dir)
    mic_test: Callable[[], tuple[bool, str]] = mic_level_test
    install: Callable[[str], tuple[bool, str]] = pip_install
    run: Callable[[list[str]], tuple[bool, str]] = run_command
    download: Callable[[str, Path], tuple[bool, str]] = download
    probe: Callable[[], Awaitable[dict[str, str]]] | None = None
    telegram_me: Callable[[str], Awaitable[str]] | None = None
    pairing: Callable[[], Awaitable[str]] | None = None
    hello: Callable[[], Awaitable[str]] | None = None
    hardware: Callable[[], object] | None = None  # () -> hwprobe.Hardware; None: the real computer



def default_env(directory: Path) -> Env:
    """An Env wired to the real application: key checks, Telegram, a test question."""
    env = Env(directory=directory)

    async def probe() -> dict[str, str]:
        from minty_core.context import build_context
        from minty_core.httpclient import get_client

        ctx = build_context(directory)
        return await ctx.router.probe(get_client())

    async def telegram_me(token: str) -> str:
        import httpx

        from minty_core.telegram.api import BotAPI

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            return (await BotAPI(token, client).get_me()).get("username") or ""

    async def pairing() -> str:
        from minty_core.keystore import SecretsStore
        from minty_core.telegram.runtime import pairing_instructions

        try:
            return await pairing_instructions(directory, SecretsStore(directory))
        except SystemExit as e:
            return str(e)

    async def hello() -> str:
        from minty_core.context import build_context

        ctx = build_context(directory)
        parts = [s async for s in ctx.agent.respond("Say hello in one short, friendly sentence.")]
        return " ".join(parts).strip()

    env.probe, env.telegram_me, env.pairing, env.hello = probe, telegram_me, pairing, hello
    return env
