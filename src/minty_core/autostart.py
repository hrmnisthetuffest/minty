"""Start Minty when you log in to Windows: one per-user scheduled task, made and removed on request, never silently.

Linux has systemd for this (`scripts/install_service.sh`, the unit text in `service.py`); Windows has no equivalent that needs no administrator rights except Task Scheduler's
per-user "at log on" task, so that is what this is. It runs `pythonw.exe` (the Python that opens no console window) from Minty's own virtual environment, 30 seconds after logging in so the
network and the sound system are up, with UTF-8 switched on. Minty then runs without a terminal: the panel is at http://127.0.0.1:7778 and Telegram keeps working.
`minty autostart on | off | status` calls this. Nothing here touches anything but the one task named `Minty`.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from minty_core import osutil

TASK = "Minty"
DELAY = "0000:30"  # hhmm:ss after logon
MAX_COMMAND = 255  # schtasks refuses a /TR longer than this


@dataclass
class Result:
    ok: bool
    message: str = ""


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, stdin=subprocess.DEVNULL,
                              creationflags=osutil.CREATE_NO_WINDOW if osutil.IS_WINDOWS else 0)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return done.returncode, (done.stdout or done.stderr).strip()


def background_python(root: Path) -> Path:
    """`pythonw.exe` in the virtual environment: the same interpreter with no console window."""
    scripts = Path(root) / ".venv" / "Scripts"
    return scripts / "pythonw.exe" if (scripts / "pythonw.exe").exists() or not (scripts / "python.exe").exists() else scripts / "python.exe"


def command_line(root: Path) -> str:
    return f'"{background_python(root)}" -X utf8 -m minty_core'


def create_arguments(root: Path) -> list[str]:
    return ["schtasks", "/Create", "/TN", TASK, "/TR", command_line(root), "/SC", "ONLOGON", "/DELAY", DELAY, "/RL", "LIMITED", "/F"]


def enable(root: Path, run: Callable[[list[str]], tuple[int, str]] = _run) -> Result:
    if not osutil.IS_WINDOWS:
        return Result(False, "This is for Windows. On Linux use scripts/install_service.sh (a systemd user service).")
    if not (Path(root) / ".venv" / "Scripts" / "python.exe").exists():
        return Result(False, f"There is no Minty environment at {Path(root) / '.venv'}: run install.ps1 first.")
    if len(command_line(root)) > MAX_COMMAND:
        return Result(False, "The install folder's path is too long for a scheduled task. Reinstall closer to the top of the drive (for example C:\\Minty).")
    code, out = run(create_arguments(root))
    if code != 0:
        return Result(False, f"Windows did not accept it: {out[:200]}")
    return Result(True, "Minty will start when you log in (30 seconds after, with no window). Open http://127.0.0.1:7778. Turn it off with: minty autostart off")


def disable(run: Callable[[list[str]], tuple[int, str]] = _run) -> Result:
    if not osutil.IS_WINDOWS:
        return Result(False, "This is for Windows. On Linux: systemctl --user disable --now minty-core")
    if not status(run):
        return Result(True, "Minty was not set to start at login.")
    code, out = run(["schtasks", "/Delete", "/TN", TASK, "/F"])
    return Result(code == 0, "Minty will no longer start at login." if code == 0 else f"Windows did not accept it: {out[:200]}")


def status(run: Callable[[list[str]], tuple[int, str]] = _run) -> bool:
    """True when the task exists."""
    return osutil.IS_WINDOWS and run(["schtasks", "/Query", "/TN", TASK])[0] == 0
