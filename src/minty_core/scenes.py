"""Quick-launch scenes (746): a named list of web addresses and desktop apps you listed. Nothing else can be opened:
no free commands, no shell; web addresses go to `xdg-open` (on Windows: the default browser), apps to `gtk-launch <desktop id>` (Linux desktop entries; Windows has no equivalent
that cannot also start any program, so an app in a scene is reported as not started there)."""
from __future__ import annotations

import re
import subprocess
from typing import Callable

from minty_core import osutil

APP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
URL = re.compile(r"^https?://[^\s\x00-\x1f]{1,400}$")


class SceneError(ValueError):
    pass


def parse_target(target: str) -> tuple[str, str]:
    t = (target or "").strip()
    if URL.match(t):
        return "url", t
    if t.startswith("app:") and APP_ID.match(t[4:]) and ".." not in t:
        return "app", t[4:]
    raise SceneError(f"a scene can open a web address (https://…) or an app (app:<desktop id>), not {t[:40]!r}")


def _run(argv: list[str]) -> int:
    if osutil.IS_WINDOWS:
        return 0 if argv[0] == "xdg-open" and osutil.open_with_default_app(argv[1]) else 1
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **osutil.group_kwargs())
        return 0
    except OSError:
        return 1


def launch(name: str, scenes: dict[str, list[str]], run: Callable[[list[str]], int] = _run) -> int:
    """Open everything in the scene; returns how many things were started."""
    if name not in scenes:
        raise SceneError(f"I don't have a scene called {name}")
    parsed = [parse_target(t) for t in scenes[name]]  # all valid before any is opened
    started = 0
    for kind, target in parsed:
        argv = ["xdg-open", target] if kind == "url" else ["gtk-launch", target]
        if run(argv) == 0:
            started += 1
    return started
