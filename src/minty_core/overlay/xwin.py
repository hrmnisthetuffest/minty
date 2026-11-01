"""The active window on X11, read with `xprop` (no extra package). `None` when there is none or it cannot be read;
on Wayland there is no such thing and the HUD simply stays hidden."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable


def _run(argv: list[str]) -> tuple[int, str]:
    if shutil.which(argv[0]) is None:
        return 127, f"{argv[0]} is not installed"
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=1.5, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return done.returncode, done.stdout


@dataclass(frozen=True)
class ActiveWindow:
    id: str
    title: str
    wm_class: str
    pid: int


class ActiveWindowReader:
    def __init__(self, run: Callable[[list[str]], tuple[int, str]] = _run):
        self._run = run

    def available(self) -> bool:
        code, _ = self._run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        return code == 0

    def read(self) -> ActiveWindow | None:
        code, out = self._run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        m = re.search(r"window id # (0x[0-9a-fA-F]+)", out) if code == 0 else None
        if not m or int(m.group(1), 16) == 0:
            return None
        wid = m.group(1)
        code, out = self._run(["xprop", "-id", wid, "WM_CLASS", "_NET_WM_NAME", "WM_NAME", "_NET_WM_PID"])
        if code != 0:
            return None
        cls = re.search(r'WM_CLASS\(STRING\) = "([^"]*)"', out)
        title = re.search(r'_NET_WM_NAME\(UTF8_STRING\) = "(.*)"', out) or re.search(r'WM_NAME\(STRING\) = "(.*)"', out)
        pid = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", out)
        return ActiveWindow(wid, title.group(1) if title else "", cls.group(1) if cls else "",
                            int(pid.group(1)) if pid else 0)
