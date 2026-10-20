"""Check this machine for everything Minty needs and say how to fix what is missing.

Run it on the laptop after installing:  .venv/bin/python scripts/doctor.py
Keep it in step with pyproject.toml and docs/getting-started.md: every new dependency gets a line in each.
"""
import importlib
import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path

HOME = Path.home()
DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share") / "minty" / "voices"
CONFIG = Path(os.environ.get("MINTY_CONFIG_DIR") or HOME / ".config/minty")

# (module, pip name, needed for)
PYTHON = [
    ("pydantic", "pydantic", "core"), ("httpx", "httpx", "core"), ("fastapi", "fastapi", "core + panel"),
    ("uvicorn", "uvicorn", "core + panel"), ("websockets", "websockets", "core"), ("tomli_w", "tomli-w", "core"),
    ("numpy", "numpy", "core"), ("rich", "rich", "terminal display"),
    ("sounddevice", "sounddevice", "microphone and speaker"), ("onnxruntime", "onnxruntime", "wake word, Kokoro"),
    ("faster_whisper", "faster-whisper", "speech recognition and wake by name"),
    ("piper", "piper-tts", "fallback voice"), ("kokoro_onnx", "kokoro-onnx", "natural voice"),
    ("openwakeword", "openwakeword", "wake_mode = model (see install_voice.sh)"),
    ("google.genai", "google-genai", "live mode (Gemini)"), ("playwright", "playwright", "browser plugin"),
    ("shazamio", "shazamio", "what song is this (pip install -e '.[music]')"),
]
# development only, never needed to run Minty: (module, pip name, what for)
DEV = [("pynput", "pynput", "global push-to-talk key (pip install -e '.[ptt]'); needs an X11 session"),
       ("cryptography", "cryptography", "encrypted backups of your memory and settings (pip install -e '.[backup]')"),
       ("xdist", "pytest-xdist", "run the tests in parallel: pytest -n auto (pip install -e '.[dev]')"),
       ("pypdf", "pypdf", "read text PDFs sent through Telegram (pip install -e '.[documents]')"),
       ("llama_cpp", "llama-cpp-python", "optional small local model for titles and summaries (pip install -e "
                                         "'.[local-llm]'; you supply the .gguf file)")]
# (command, what for, apt package, pacman package, optional?)
BINARIES = [
    ("playerctl", "pause music, detect music", "playerctl", "playerctl", False),
    ("pactl", "detect audio playing", "pulseaudio-utils", "libpulse", False),
    ("parec", "record audio for song recognition", "pulseaudio-utils", "libpulse", False),
    ("google-chrome-stable", "browser plugin", "(download Chrome from google.com/chrome)", "google-chrome (AUR)", True),
    ("node", "n8n, Claude Code, OpenClaw", "nodejs npm", "nodejs npm", True),
    ("n8n", "n8n plugin", "npm install -g n8n", "npm install -g n8n", True),
    ("claude", "Claude Code plugin", "curl -fsSL https://claude.ai/install.sh | bash", "same", True),
    ("hermes", "Hermes Agent plugin", "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash; then hermes setup", "same", True),
    ("openclaw", "OpenClaw plugin", "curl -fsSL https://openclaw.ai/install.sh | bash; then openclaw onboard --install-daemon", "same", True),
    ("spotify", "Spotify plugin", "flatpak install flathub com.spotify.Client (Mint blocks snap)", "spotify (AUR) or the same flatpak", True),
    ("pipx", "installer plugin (pipx)", "pipx", "python-pipx", True),
    ("xprop", "the HUD's active-window check (minty-core overlay; X11 only)", "x11-utils", "xorg-xprop", True),
    ("git", "a history of every change to your notes folder (vault.git)", "git", "git", True),
    ("tesseract", "read text in pictures sent through Telegram", "tesseract-ocr", "tesseract tesseract-data-eng", True),
    ("yt-dlp", "YouTube captions summary (web.helpers; optional, your install: pipx install yt-dlp)", "yt-dlp", "yt-dlp", True),
    ("rsvg-convert", "the daily card as a picture (selfcheck.card_push; Chrome also works)", "librsvg2-bin", "librsvg", True),
    ("playerctl", "the sleep timer pauses the player (integrations.sleep_timer)", "playerctl", "playerctl", True),
    ("ping", "the network inventory (integrations.lan)", "iputils-ping", "iputils", True),
    ("ip", "the network inventory reads the neighbour table", "iproute2", "iproute2", True),
    ("npx", "MCP servers from the directory that are npm packages (Node)", "nodejs npm", "nodejs npm", True),
    ("uvx", "MCP servers from the directory that are Python packages (uv)", "uv", "uv", True),
    ("ffmpeg", "clip trim/merge and lecture notes from audio (create.enabled)", "ffmpeg", "ffmpeg", True),
    ("ffprobe", "clip lengths for the media tools (comes with ffmpeg)", "ffmpeg", "ffmpeg", True),
    ("gh", "CI status and the CI watcher (dev.ci_watch); then run: gh auth login", "gh", "github-cli", True),
    ("wpctl", "volume and sound output by voice (system.controls)", "wireplumber", "wireplumber", True),
    ("powerprofilesctl", "power profile by voice", "power-profiles-daemon", "power-profiles-daemon", True),
    ("brightnessctl", "screen brightness by voice (without it: software dimming only)", "brightnessctl", "brightnessctl", True),
    ("udisksctl", "USB mount and eject", "udisks2", "udisks2", True),
    ("coredumpctl", "crash triage", "systemd-coredump", "systemd", True),
    ("checkupdates", "update advisor on Arch (better than pacman -Qu; not needed on Mint)", "(Arch only)", "pacman-contrib", True),
    ("dbus-monitor", "the notification reader (system.notifications)", "dbus-bin", "dbus", True),
    ("gsettings", "night light on Cinnamon", "libglib2.0-bin", "glib2", True),
]
FILES = [
    (DATA / "kokoro-v1.0.onnx", "Kokoro model (scripts/install_voice.sh)"),
    (DATA / "voices-v1.0.bin", "Kokoro voices (scripts/install_voice.sh)"),
    (DATA / "en_US-lessac-medium.onnx", "Piper voice (scripts/install_voice.sh)"),
]


def ok(flag: bool) -> str:
    return "OK     " if flag else "MISSING"


def main() -> int:
    missing = 0
    print(f"Python {platform.python_version()} on {platform.machine()}  (needs 3.10+)")
    if sys.version_info < (3, 10):
        missing += 1
    try:
        flags = set(next(l for l in open("/proc/cpuinfo") if l.startswith("flags")).split(":")[1].split())
        print(f"CPU: avx={'avx' in flags} avx2={'avx2' in flags}  "
              f"{'(no AVX2: prefer classic voice mode with Piper, expect Kokoro to be slow)' if 'avx2' not in flags else ''}")
    except (OSError, StopIteration):
        pass
    print("\nPython packages (in this venv):")
    for module, pip_name, why in PYTHON:
        try:
            importlib.import_module(module)
            found = True
        except Exception:
            found = False
        missing += not found
        print(f"  [{ok(found)}] {pip_name:16} {why}")
    for module, pip_name, why in DEV:  # informational: never counted as missing
        found = importlib.util.find_spec(module) is not None
        print(f"  [{'OK     ' if found else 'dev    '}] {pip_name:16} {why}")
    try:
        importlib.import_module("sherpa_onnx")
        have = True
    except Exception:
        have = False
    print(f"  [{'OK     ' if have else 'optional'}] {'sherpa-onnx':16} word-by-word captions "
          "(pip install sherpa-onnx, then audio.streaming_asr = \"sherpa\")")
    try:  # the browser reads pages through Playwright's accessibility snapshot (mode="ai"), new in 1.63
        from importlib.metadata import version

        major, minor = (int(x) for x in version("playwright").split(".")[:2])
        fresh = (major, minor) >= (1, 63)
        print(f"  [{ok(fresh)}] {'playwright >= 1.63':16} accessibility snapshot for the browser plugin "
              f"(have {version('playwright')})")
        missing += not fresh
    except Exception:
        pass
    try:  # the speech detector that keeps noise from becoming words ships inside faster-whisper
        from faster_whisper.vad import get_assets_path
        vad = (Path(get_assets_path()) / "silero_vad_v6.onnx").exists() or any(
            p.name.startswith("silero_vad") for p in Path(get_assets_path()).iterdir())
    except Exception:
        vad = False
    missing += not vad
    print(f"  [{ok(vad)}] {'silero VAD':16} speech detector (inside faster-whisper; noise is never transcribed)")
    print("\nPrograms:")
    for command, why, apt, pacman, optional in BINARIES:
        found = shutil.which(command) is not None
        missing += (not found) and not optional
        tag = ok(found) if found or not optional else "optional"
        print(f"  [{tag:7}] {command:22} {why}" + ("" if found else f"   -> apt: {apt} | pacman: {pacman}"))
    print("\nModel files:")
    for path, why in FILES:
        found = path.is_file()
        missing += not found
        print(f"  [{ok(found)}] {path.name:28} {why}")
    print("\nSetup:")
    keys = CONFIG / "secrets.toml"
    print(f"  [{ok(keys.is_file())}] API keys           minty-core import-keys <file>   or   minty-core set-key <name>")
    print(f"  [{ok((CONFIG / 'panel_auth.toml').is_file())}] panel password    minty set-password")
    try:
        import tomllib

        data = tomllib.loads(keys.read_text()) if keys.is_file() else {}
        names = set(data.get("keys", data))  # key names only; values are never read out
    except (OSError, ValueError):
        names = set()
    spotify = {"plugin.spotify.client_id", "plugin.spotify.client_secret"} <= names
    signed_in = "plugin.spotify.refresh_token" in names
    print(f"  [{'OK     ' if spotify else 'optional'}] Spotify app keys  minty-core set-key plugin.spotify.client_id / client_secret")
    print(f"  [{'OK     ' if signed_in else 'optional'}] Spotify sign-in   minty-core spotify-login")
    print(f"  [{'OK     ' if 'plugin.mail.password' in names else 'optional'}] Mail app password  "
          "minty-core set-key plugin.mail.password   (plugins.mail.user in config.toml)")
    print(f"  [{'OK     ' if (CONFIG / 'school.json').is_file() else 'optional'}] SchoolPad address   say "
          "\"set my school portal to <address>\", then log in once in the browser window")
    print(f"  [{'OK     ' if 'fish' in names else 'optional'}] Fish Audio key    minty-core set-key fish   (tts.engine = fish)")
    print(f"  [{'OK     ' if 'hevy' in names else 'optional'}] Hevy API key       minty-core set-key hevy   "
          "(needs Hevy Pro; then turn on health.hevy and agree to training data)")
    print(f"  [{'OK     ' if 'telegram' in names else 'optional'}] Telegram bot token minty-core set-key telegram   "
          "then minty-core telegram-pair (no extra package: it uses httpx)")
    print("\nAll required items present." if not missing else f"\n{missing} required item(s) missing.")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
