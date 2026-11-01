"""Does this installation work? Runs the things that go wrong on a fresh machine and says OK, WARN or FAIL for each, with what to do.

    Windows:  scripts\\windows_check.ps1                (it finds the right Python and keeps a copy of the output)
    Linux:    .venv/bin/python scripts/install_check.py
    Options:  --quick   skip the check that really starts Minty        --no-start   same        --timeout N   seconds to wait for the panel (default 90)

It uses a scratch settings folder and free ports. The only thing it does in your real settings folder is create and delete a tiny test file to prove the folder can be written to; your
settings, keys and notes are not read or changed. Exit status 1 when anything FAILs.
When something fails, send the whole output (the .ps1 saves it to minty-check.txt in your temp folder).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import locale
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # a checkout works before (or without) an install

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
CORE_MODULES = ("pydantic", "httpx", "fastapi", "uvicorn", "websockets", "tomli_w", "numpy", "rich", "tzdata" if sys.platform == "win32" else "zoneinfo")
VOICE_MODULES = (("sounddevice", "microphone and speaker"), ("onnxruntime", "wake word and voices"), ("faster_whisper", "speech recognition"),
                 ("kokoro_onnx", "natural voice"), ("piper", "fallback voice"))
OPTIONAL_PROGRAMS = (("ffmpeg", "videos and audio notes"), ("git", "history of your notes folder"), ("node", "n8n and Claude Code"), ("claude", "Claude Code plugin"))


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))
        first, *rest = (detail or "").splitlines() or [""]
        print(f"[{status}] {name:22} {first}")
        for line in rest:
            print(f"{'':31}{line}")
        sys.stdout.flush()

    @property
    def failed(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == FAIL)

    @property
    def warned(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == WARN)


def check(report: Report, name: str, fn) -> None:
    """Run one check. It returns (status, detail), or raises: an exception is a FAIL that says what it was."""
    try:
        status, detail = fn()
    except Exception as e:  # noqa: BLE001 — the point of this script is to report, not to crash
        status, detail = FAIL, f"{type(e).__name__}: {e}"
    report.add(status, name, detail)


# ---- the checks ------------------------------------------------------------------------------------------------------------------------
def system() -> tuple[str, str]:
    v = sys.version_info
    where = "in a virtual environment" if sys.prefix != getattr(sys, "base_prefix", sys.prefix) else "NOT in a virtual environment"
    text = f"{platform.platform()} ({platform.machine()}); Python {v.major}.{v.minor}.{v.micro} {where}\n{sys.executable}"
    return (OK if v >= (3, 10) else FAIL), text + ("" if v >= (3, 10) else "\nMinty needs Python 3.10 or newer.")


def imports() -> tuple[str, str]:
    missing = []
    for name in CORE_MODULES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    fix = "Run the installer again, or: python -m pip install -e <the Minty folder>"
    if missing:
        return FAIL, f"missing: {', '.join(missing)}. {fix}"
    try:
        from minty_core import __version__
        import minty_core.app  # noqa: F401  (the whole application imports)
    except ImportError as e:
        return FAIL, f"Minty itself does not load ({type(e).__name__}: {e}). {fix}"
    return OK, f"Minty {__version__} and its {len(CORE_MODULES)} core packages"


def utf8() -> tuple[str, str]:
    from minty_core import fsutil

    sample = "café — 日本語 ❤"
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "t.txt"
        fsutil.atomic_write_text(target, sample + "\nsecond line\n")
        raw = target.read_bytes()
        if raw != (sample + "\nsecond line\n").encode("utf-8"):
            return FAIL, f"text came back different on disk: {raw[:40]!r} (line ends or encoding were changed)"
    default = locale.getpreferredencoding(False)
    note = "" if (sys.flags.utf8_mode or default.lower().replace("-", "") == "utf8") else f"\nPython's default text encoding here is {default}; Minty always says UTF-8 itself, so this only matters to other programs"
    return (WARN if note else OK), f"accents, dashes and Japanese survive a file round trip; stdout is {getattr(sys.stdout, 'encoding', '?')}{note}"


def folders() -> tuple[str, str]:
    from minty_core.paths import config_dir, data_dir

    config = config_dir()
    config.mkdir(parents=True, exist_ok=True)
    probe = config / ".write-test"
    probe.write_text("x", encoding="utf-8")
    probe.unlink()
    return OK, f"settings: {config}\n         models: {data_dir()}"


def single_instance() -> tuple[str, str]:
    from minty_core import instance

    with tempfile.TemporaryDirectory() as tmp:
        first = instance.acquire(Path(tmp))
        try:
            instance.acquire(Path(tmp))
        except instance.AlreadyRunning as e:
            os.close(first)
            return OK, f"a second Minty is refused and told who holds it (process {e.pid})"
        os.close(first)
    return FAIL, "the lock did not stop a second Minty"


def process_tree() -> tuple[str, str]:
    from minty_core import osutil

    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "grandchild"
        code = ("import subprocess, sys, time\n"
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
                "time.sleep(120)\n")
        proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **osutil.group_kwargs())
        for _ in range(100):
            if marker.exists() and marker.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        else:
            osutil.kill_tree(proc)
            return FAIL, "the test program did not start"
        grandchild = int(marker.read_text(encoding="utf-8"))
        osutil.kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return FAIL, "the program was not stopped"
        time.sleep(0.5)
        if alive(grandchild):
            return FAIL, "stopping a program left what it started still running"
    return OK, "stopping a program stops everything it started"


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        done = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        return str(pid) in done.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def timezones() -> tuple[str, str]:
    from zoneinfo import ZoneInfo

    ZoneInfo("Europe/Berlin")
    ZoneInfo("Asia/Tokyo")
    return OK, "time zones are available (\"what time is it in Tokyo\" works)"


def brain() -> tuple[str, str]:
    from minty_core.context import build_context

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows cannot delete a database file that is still open (WinError 32); the check is about the second brain, not the cleanup
        ctx = build_context(Path(tmp))
        services = ctx.services

        async def go() -> str:
            got = await services["knowledge_capture"].capture("Idea: sell a monthly tasting box to small cafés")
            if got["status"] != "filed":
                return f"capture ended as {got['status']}: {got.get('error', '')}"
            hits = services["knowledge_reason"].search("tasting box cafes")
            return "" if hits and "caf" in hits[0]["snippet"] else "what was captured could not be found again"

        problem = asyncio.run(go())
        services["knowledge"].close()
    return (FAIL, problem) if problem else (OK, "a thought is captured, filed and found again (the second brain works)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def starts(timeout: int) -> tuple[str, str]:
    """Really start Minty on free ports with a scratch settings folder and wait for the panel to answer."""
    from minty_core import osutil

    api, panel = free_port(), free_port()
    tmp = Path(tempfile.mkdtemp(prefix="minty-check-"))
    (tmp / "config.toml").write_text(f"api_port = {api}\n", encoding="utf-8")
    (tmp / "panel.toml").write_text(f"port = {panel}\n", encoding="utf-8")
    env = dict(os.environ, MINTY_CONFIG_DIR=str(tmp), PYTHONUTF8="1", PYTHONPATH=str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    log = tmp / "start.log"
    started = time.monotonic()
    with log.open("wb") as out:
        proc = subprocess.Popen([sys.executable, "-m", "minty_core", "--safe"], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env, **osutil.group_kwargs())
        answer = ""
        try:
            while time.monotonic() - started < timeout:
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{panel}/", timeout=2) as r:
                        if r.status == 200 and b"<" in r.read(200):
                            answer = f"the panel answered after {time.monotonic() - started:.0f} s"
                            break
                except OSError:
                    time.sleep(0.5)
        finally:
            osutil.terminate_tree(proc)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                osutil.kill_tree(proc)
                proc.wait(timeout=10)
    tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-12:])
    shutil.rmtree(tmp, ignore_errors=True)
    if answer:
        return OK, f"Minty started on its own ports and {answer}, and stopped when asked"
    return FAIL, "Minty did not come up. The end of its log:\n" + textwrap.indent(tail or "(nothing was written)", "    ")


def voice() -> tuple[str, str]:
    missing = []
    for name, what in VOICE_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001 — a DLL that will not load raises more than ImportError
            missing.append(f"{name} ({what}): {type(e).__name__}: {str(e)[:80]}")
    if missing:
        extra = "\n".join(missing)
        hint = "\nVoice is optional. To add it:  python -m pip install -e \".[voice]\"" + ("\nA 'DLL load failed' here is usually the Visual C++ runtime:  winget install Microsoft.VCRedist.2015+.x64" if sys.platform == "win32" else "")
        return WARN, extra + hint
    return OK, "the voice packages load"


def audio() -> tuple[str, str]:
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        return WARN, "sounddevice is not installed (voice is optional)"
    try:
        inp, out = sd.query_devices(kind="input"), sd.query_devices(kind="output")
    except Exception as e:  # noqa: BLE001 — no device raises PortAudioError
        return WARN, f"no microphone or speaker found ({type(e).__name__}). Voice needs both; typing and the panel work without."
    return OK, f"microphone: {inp['name']}\nspeaker:    {out['name']}"


def programs() -> tuple[str, str]:
    lines = [f"{name:8} {'found' if shutil.which(name) else 'not found':10} {what}" for name, what in OPTIONAL_PROGRAMS]
    missing = [n for n, _ in OPTIONAL_PROGRAMS if not shutil.which(n)]
    return (WARN if missing else OK), "optional, each only for the feature named:\n" + "\n".join(lines)


def autostart_state() -> tuple[str, str]:
    from minty_core import autostart, osutil

    if not osutil.IS_WINDOWS:
        return OK, "not applicable on this system (Linux uses a systemd service: scripts/install_service.sh)"
    return OK, "Minty starts when you log in" if autostart.status() else "Minty does not start at login (turn it on with: minty autostart on)"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", "--no-start", dest="quick", action="store_true", help="skip the check that really starts Minty")
    parser.add_argument("--timeout", type=int, default=90, help="seconds to wait for the panel")
    args = parser.parse_args(argv)
    print(f"Minty install check ({time.strftime('%Y-%m-%d %H:%M')})\n")
    report = Report()
    for name, fn in (("system", system), ("packages", imports), ("text encoding", utf8), ("folders", folders), ("one Minty at a time", single_instance), ("stopping programs", process_tree),
                     ("time zones", timezones), ("second brain", brain)):
        check(report, name, fn)
    if not args.quick:
        check(report, "starts and answers", lambda: starts(args.timeout))
    for name, fn in (("voice packages", voice), ("microphone", audio), ("optional programs", programs), ("start at login", autostart_state)):
        check(report, name, fn)
    print(f"\n{len(report.rows)} checks: {len(report.rows) - report.failed - report.warned} OK, {report.warned} WARN (optional things), {report.failed} FAIL")
    print("Everything Minty needs works." if not report.failed else "Something Minty needs does not work: send this whole output.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
