"""The one place system commands run. Exact argument lists from an allow-list, never a shell, never `sudo`, a plain
environment (no keys), a timeout and an output cap. A missing program is a result, not a crash. Each program may also have
an argument rule, so `pacman` can only query, never install."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from typing import Callable

DEFAULT_ALLOWED = frozenset({
    "wpctl", "pactl", "brightnessctl", "powerprofilesctl", "loginctl", "systemctl", "udisksctl", "coredumpctl",
    "journalctl", "gsettings", "pacman", "checkupdates", "apt", "dpkg", "lsblk", "xrandr", "dbus-monitor", "xdg-open",
    "xdg-mime", "ps", "file", "du", "df", "stat"})
_PASS_ENV = ("PATH", "HOME", "USER", "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_SESSION_ID",
             "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP")


def _only(prefixes: tuple[str, ...], position: int = 1) -> Callable[[list[str]], bool]:
    return lambda argv: len(argv) > position and argv[position].startswith(prefixes)


_JOURNAL_WRITES = ("--rotate", "--vacuum", "--flush", "--sync", "--relinquish", "--smart-relinquish", "--setup-keys",
                   "--update-catalog", "--verify", "--header")


ALLOWED_GSETTINGS = {("org.cinnamon.settings-daemon.plugins.color", "night-light-enabled"),
                     ("org.cinnamon.settings-daemon.plugins.color", "night-light-temperature")}
_SINK = ("@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@")


def _wpctl_ok(a: list[str]) -> bool:
    if len(a) == 2:
        return a[1] == "status"
    if a[1] == "get-volume" and len(a) == 3:
        return a[2] in _SINK
    if a[1] == "set-mute" and len(a) == 4:
        return a[2] in _SINK and a[3] in ("0", "1", "toggle")
    if a[1] == "set-default" and len(a) == 3:
        return a[2].isdigit()
    if a[1] == "set-volume" and len(a) == 4 and a[2] in _SINK:
        v = a[3]
        if re.fullmatch(r"\d{1,2}%[+-]", v):
            return True
        return re.fullmatch(r"\d(?:\.\d{1,2})?", v) is not None and float(v) <= 1.0
    return False


def _xrandr_brightness(a: list[str]) -> bool:
    return (len(a) == 5 and a[1] == "--output" and re.fullmatch(r"[A-Za-z0-9-]{1,20}", a[2]) is not None
            and a[3] == "--brightness" and re.fullmatch(r"[01](?:\.\d{1,2})?", a[4]) is not None
            and 0.3 <= float(a[4]) <= 1.0)


def _gsettings_ok(a: list[str]) -> bool:
    if len(a) < 2:
        return False
    if a[1] == "set":
        return len(a) == 5 and (a[2], a[3]) in ALLOWED_GSETTINGS
    return a[1] in ("get", "list-schemas", "list-keys", "range")


def _verbs(argv: list[str]) -> list[str]:
    return [a for a in argv[1:] if a != "--user"]


RULES: dict[str, Callable[[list[str]], bool]] = {
    "pacman": _only(("-Q", "-Si", "-Ss", "-Sl")),  # queries only: never -S, -R or -U
    "apt": lambda a: len(a) > 1 and a[1] in ("list", "show", "policy", "search"),
    "dpkg": _only(("-S", "-l", "-s", "--search", "--status")),
    "systemctl": lambda a: bool(_verbs(a)) and _verbs(a)[0] in ("status", "is-active", "is-failed", "list-units", "--failed",
                                                                "show", "suspend", "list-unit-files", "cat"),
    "journalctl": lambda a: not any(x.startswith(_JOURNAL_WRITES) for x in a[1:]),
    "coredumpctl": lambda a: len(a) > 1 and a[1] in ("list", "info"),
    "loginctl": lambda a: len(a) > 1 and a[1] in ("lock-session", "show-session", "list-sessions"),
    "udisksctl": lambda a: len(a) == 4 and a[1] in ("mount", "unmount", "power-off", "info") and a[2] == "-b"
    and re.fullmatch(r"/dev/(?:sd[a-z]+\d*|nvme\d+n\d+(?:p\d+)?|mmcblk\d+(?:p\d+)?|sr\d+)", a[3]) is not None,
    "lsblk": lambda a: a[1:2] == ["-J"],
    "gsettings": lambda a: _gsettings_ok(a),
    "xdg-open": lambda a: len(a) == 2 and not a[1].startswith("-"),
    "dbus-monitor": lambda a: True,
    "powerprofilesctl": lambda a: (len(a) == 2 and a[1] in ("get", "list")) or (
        len(a) == 3 and a[1] == "set" and a[2] in ("performance", "balanced", "power-saver")),
    "wpctl": lambda a: _wpctl_ok(a),
    "xrandr": lambda a: a[1:] == ["--query"] or _xrandr_brightness(a),
    "brightnessctl": lambda a: a[1:] == ["-m"] or (len(a) == 3 and a[1] == "set" and re.fullmatch(r"\d{1,3}%", a[2])
                                                    is not None and 0 < int(a[2][:-1]) <= 100),
}


@dataclass
class Result:
    code: int
    out: str = ""
    err: str = ""
    missing: bool = False
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.missing and not self.timed_out


class Runner:
    def __init__(self, allowed=DEFAULT_ALLOWED, timeout: float = 15.0, max_out: int = 40000,
                 which: Callable[[str], str | None] = shutil.which, rules=RULES):
        self.allowed, self.timeout, self.max_out, self._which, self._rules = set(allowed), timeout, max_out, which, rules

    def have(self, name: str) -> bool:
        return self._which(name) is not None

    def _check(self, argv: list[str]) -> None:
        if not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("A command needs a program and text arguments.")
        name = argv[0]
        if "/" in name or name not in self.allowed:
            raise ValueError(f"{name!r} is not on the list of programs I may run.")
        rule = self._rules.get(name)
        if rule is not None and not rule(argv):
            raise ValueError(f"That use of {name} is not allowed.")

    async def run(self, argv: list[str], timeout: float | None = None, cwd=None) -> Result:
        self._check(argv)
        path = self._which(argv[0])
        if path is None:
            return Result(127, err=f"{argv[0]} is not installed", missing=True)
        env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
        env["LC_ALL"] = "C"
        proc = await asyncio.create_subprocess_exec(path, *argv[1:], stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    env=env, cwd=cwd)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout or self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return Result(124, err="timed out", timed_out=True)
        cut = lambda b: b.decode("utf-8", "replace")[:self.max_out]  # noqa: E731
        return Result(proc.returncode or 0, cut(out), cut(err))
