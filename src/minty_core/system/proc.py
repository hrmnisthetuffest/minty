"""Load, memory and per-process CPU read straight from /proc (122), the heavy-work gate, and the process explainer (645).
psutil is not installed here and nothing was downloaded: this is the small part of it that is needed, and one function each
to swap if it is ever added."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PROC = Path("/proc")

# what common programs are, in a few plain words; anything not here is described by where it lives
KNOWN = {
    "systemd": "the program that starts and supervises everything else on this computer",
    "pipewire": "the sound server: it moves audio between programs and speakers",
    "pipewire-pulse": "the sound server's compatibility layer for older audio programs",
    "wireplumber": "the part of the sound system that decides which device plays what",
    "pulseaudio": "the older sound server",
    "xorg": "the display server that draws the screen",
    "cinnamon": "the desktop itself: panel, windows and menus",
    "cinnamon-session": "the program that starts your desktop when you log in",
    "nemo": "the file manager",
    "firefox": "the Firefox web browser", "chrome": "the Chrome web browser", "chromium": "the Chromium web browser",
    "google-chrome": "the Chrome web browser", "code": "the Visual Studio Code editor",
    "dbus-daemon": "the message bus programs use to talk to each other",
    "networkmanager": "the program that manages Wi-Fi and network connections",
    "polkitd": "the program that decides which actions need an administrator's yes",
    "sshd": "the remote-login server (it lets other computers log in)",
    "cupsd": "the printing service", "bluetoothd": "the Bluetooth service",
    "bash": "a terminal shell", "zsh": "a terminal shell", "fish": "a terminal shell",
    "node": "a JavaScript program", "python": "a Python program", "python3": "a Python program",
    "spotify": "the Spotify music player", "telegram-desktop": "the Telegram app",
    "gnome-terminal-server": "the terminal window program", "ps": "the process-listing command itself",
}
# ending these breaks the desktop or the whole system
CORE = {"systemd", "xorg", "cinnamon", "cinnamon-session", "dbus-daemon", "pipewire", "wireplumber", "networkmanager",
        "init", "login", "polkitd"}


@dataclass
class Proc:
    pid: int
    name: str
    ppid: int = 0
    state: str = ""
    ticks: int = 0  # user + system CPU time so far, in clock ticks
    rss_kb: int = 0
    start_ticks: int = 0
    cmdline: list[str] = field(default_factory=list)
    uid: int = -1
    exe: str = ""


def _text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def loadavg(root: Path = PROC) -> tuple[float, float, float]:
    parts = _text(Path(root) / "loadavg").split()
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (IndexError, ValueError):
        return 0.0, 0.0, 0.0


def memory(root: Path = PROC) -> tuple[int, int]:
    """(total, available) in kB."""
    found = {}
    for line in _text(Path(root) / "meminfo").splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable") and rest.split():
            found[key] = int(rest.split()[0])
    return found.get("MemTotal", 0), found.get("MemAvailable", 0)


def read_procs(root: Path = PROC, page_kb: int | None = None) -> list[Proc]:
    root = Path(root)
    page_kb = page_kb or max(1, os.sysconf("SC_PAGE_SIZE") // 1024)
    out = []
    for d in sorted((p for p in root.iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
        stat = _text(d / "stat")
        head, sep, tail = stat.rpartition(") ")
        if not sep:
            continue  # it exited while we looked
        parts = tail.split()
        try:
            name = head.split("(", 1)[1]
            proc = Proc(int(d.name), name, int(parts[1]), parts[0], int(parts[11]) + int(parts[12]),
                        int(parts[21]) * page_kb, int(parts[19]))
        except (IndexError, ValueError):
            continue
        proc.cmdline = [c for c in _text(d / "cmdline").split("\0") if c]
        for line in _text(d / "status").splitlines():
            if line.startswith("Uid:"):
                proc.uid = int(line.split()[1])
        try:
            proc.exe = os.readlink(d / "exe")
        except OSError:
            pass
        out.append(proc)
    return out


class CpuSampler:
    """CPU percent of one core per process between two samples (the first sample has nothing to compare, so reads 0)."""

    def __init__(self, read: Callable[[], list[Proc]] = read_procs, clock: Callable[[], float] = time.monotonic,
                 clk_tck: int | None = None, cpus: int | None = None):
        self._read, self._clock = read, clock
        self.clk_tck = clk_tck or os.sysconf("SC_CLK_TCK")
        self.cpus = cpus or os.cpu_count() or 1
        self._last: dict[int, int] = {}
        self._at: float | None = None

    def sample(self) -> list[tuple[Proc, float]]:
        now, procs = self._clock(), self._read()
        dt = (now - self._at) if self._at is not None else 0.0
        rows = []
        for p in procs:
            before = self._last.get(p.pid)
            pct = (p.ticks - before) / (self.clk_tck * dt) * 100.0 if before is not None and dt > 0 else 0.0
            rows.append((p, max(0.0, pct)))
        self._last, self._at = {p.pid: p.ticks for p in procs}, now
        return rows


def top_cpu(rows: list[tuple[Proc, float]], n: int = 3, min_percent: float = 0.0) -> list[tuple[Proc, float]]:
    return sorted((r for r in rows if r[1] > min_percent), key=lambda r: -r[1])[:n]


@dataclass
class Decision:
    ok: bool
    reason: str = ""


class ResourceGate:
    """Is this a good moment for heavy background work? (122) Says no, and why, when the machine is busy, short of memory,
    on a low battery or hot."""

    def __init__(self, load=loadavg, mem=memory, cpus=lambda: os.cpu_count() or 1, power=lambda: (None, None, False),
                 max_load_per_cpu: float = 0.9, min_mem_fraction: float = 0.15, battery_floor: int = 30):
        self._load, self._mem, self._cpus, self._power = load, mem, cpus, power
        self.max_load, self.min_mem, self.floor = max_load_per_cpu, min_mem_fraction, battery_floor

    def check(self) -> Decision:
        on_ac, pct, hot = self._power()
        if hot:
            return Decision(False, "the computer is running hot")
        if on_ac is False and pct is not None and pct <= self.floor:
            return Decision(False, f"it is on battery at {pct} percent")
        load, cpus = self._load()[0], max(1, self._cpus())
        if load / cpus > self.max_load:
            return Decision(False, f"the computer is busy (load {load:.1f} on {cpus} cores)")
        total, avail = self._mem()
        if total and avail / total < self.min_mem:
            return Decision(False, f"memory is low ({avail * 100 // total} percent free)")
        return Decision(True)


def _age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} seconds"
    if seconds < 5400:
        return f"{int(seconds // 60)} minutes"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} hours"
    return f"{int(seconds // 86400)} days"


def _size(kb: int) -> str:
    return f"{kb / 1048576:.1f} GB" if kb >= 1048576 else f"{max(1, kb // 1024)} MB"


def explain_process(procs: list[Proc], query: str, uptime_s: float, clk_tck: int | None = None) -> str:
    clk = clk_tck or os.sysconf("SC_CLK_TCK")
    q = str(query).strip().lower()
    if q.isdigit():
        found = [p for p in procs if p.pid == int(q)]
    else:
        found = [p for p in procs if p.name.lower() == q] or [p for p in procs if q and q in p.name.lower()]
    if not found:
        return f"No running program matches {query!r}."
    p = max(found, key=lambda x: (x.ticks, x.rss_kb))
    parent = next((x.name for x in procs if x.pid == p.ppid), "")
    low = p.name.lower()
    if any("minty_core" in c or c.endswith("/minty") for c in p.cmdline) or low == "minty":
        return f"That is me (pid {p.pid}): Minty, running for {_age(max(0, uptime_s - p.start_ticks / clk))}."
    what = KNOWN.get(low)
    kernel = not p.cmdline and p.ppid in (0, 2)
    if kernel:
        what = "a kernel thread: part of Linux itself, not a program you started"
    parts = [f"{p.name} (pid {p.pid}) is {what}." if what else f"I don't know this program: {p.name} (pid {p.pid})."]
    if what is None:
        where = p.exe or (p.cmdline[0] if p.cmdline else "an unknown place")
        parts.append(f"It lives at {where}" + (f" and was run as: {' '.join(p.cmdline)[:120]}" if p.cmdline else "") + ".")
    parts.append(f"It has been running for {_age(max(0, uptime_s - p.start_ticks / clk))}, has used {p.ticks / clk:.0f} "
                 f"seconds of processor time and holds {_size(p.rss_kb)} of memory.")
    if parent:
        parts.append(f"It was started by {parent}.")
    if len(found) > 1:
        parts.append(f"There are {len(found)} programs with that name; this is the busiest.")
    if low in CORE:
        parts.append("Do not end it: the desktop or the whole system depends on it.")
    elif not kernel:
        parts.append(f"I will not end programs on my own. To stop it yourself: kill {p.pid} in a terminal "
                     "(kill -9 only if that does nothing).")
    return " ".join(parts)


async def owner_package(os_, runner, exe: str) -> str:
    """Which package owns this file, using the package manager of THIS system ('' if unknown)."""
    if not exe or os_.manager not in ("pacman", "apt"):
        return ""
    argv = ["pacman", "-Qo", exe] if os_.manager == "pacman" else ["dpkg", "-S", exe]
    res = await runner.run(argv)
    if not res.ok:
        return ""
    line = res.out.strip().splitlines()[0] if res.out.strip() else ""
    if os_.manager == "pacman":
        return line.split(" is owned by ")[1].split()[0] if " is owned by " in line else ""
    return line.split(":")[0].strip() if ":" in line else ""
