"""Facts about this computer and what is playing on it, read from /proc, /sys and the music watcher. Nothing leaves the machine."""
from __future__ import annotations

import getpass
import platform
import re
import shutil
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from minty_core.instant.base import Query, Rule
from minty_core.instant.text import num, say_duration

CATEGORY = "this computer"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _local_ip() -> str | None:
    """The address this computer uses on the local network. A UDP "connect" sends nothing; it only picks the route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


@dataclass
class Machine:
    """What the computer can say about itself. `root` is where /proc and /sys live, so a test can describe any machine."""
    root: Path = Path("/")
    home: Path = field(default_factory=Path.home)
    hostname: Callable[[], str] = socket.gethostname
    username: Callable[[], str] = getpass.getuser
    kernel: Callable[[], str] = platform.release
    local_ip: Callable[[], str | None] = _local_ip
    disk_usage: Callable[[Path], tuple[int, int, int]] = lambda p: tuple(shutil.disk_usage(p))  # total, used, free

    def battery(self) -> tuple[int, str] | None:
        base = self.root / "sys/class/power_supply"
        for bat in sorted(base.glob("BAT*")) if base.is_dir() else []:
            capacity = _read(bat / "capacity").strip()
            if capacity.isdigit():
                return int(capacity), _read(bat / "status").strip().lower() or "unknown"
        return None

    def memory_mb(self) -> tuple[int, int] | None:
        """(used, total) in MB; "used" is what is not available to programs."""
        fields: dict[str, int] = {}
        for line in _read(self.root / "proc/meminfo").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                fields[key] = int(parts[0]) // 1024
        if "MemTotal" not in fields or "MemAvailable" not in fields:
            return None
        return fields["MemTotal"] - fields["MemAvailable"], fields["MemTotal"]

    def disk_gb(self) -> tuple[float, float] | None:
        try:
            total, _, free = self.disk_usage(self.home)
        except OSError:
            return None
        return free / 1e9, total / 1e9

    def threads(self) -> int:
        n = sum(1 for line in _read(self.root / "proc/cpuinfo").splitlines() if line.startswith("processor"))
        return n or 1

    def load_percent(self) -> int | None:
        first = _read(self.root / "proc/loadavg").split()
        try:
            return min(100, round(float(first[0]) / self.threads() * 100))
        except (IndexError, ValueError):
            return None

    def cpu_model(self) -> str | None:
        for line in _read(self.root / "proc/cpuinfo").splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() in ("model name", "hardware", "model") and value.strip():
                return " ".join(value.split())
        return None

    def uptime_s(self) -> float | None:
        try:
            return float(_read(self.root / "proc/uptime").split()[0])
        except (IndexError, ValueError):
            return None

    def temperature_c(self) -> float | None:
        base = self.root / "sys/class/thermal"
        readings = []
        for zone in sorted(base.glob("thermal_zone*")) if base.is_dir() else []:
            raw = _read(zone / "temp").strip()
            if raw.lstrip("-").isdigit():
                readings.append(int(raw) / 1000)
        return max(readings) if readings else None

    def os_name(self) -> str | None:
        for line in _read(self.root / "etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
        return None


_DEFAULT = Machine()


def _machine(q: Query) -> Machine:
    return q.env.system or _DEFAULT  # type: ignore[return-value]


def _gb(mb: float) -> str:
    return f"{num(round(mb / 1024, 1))} GB"


_BATTERY = re.compile(r"^(?:how much battery(?: do i have| is left| is there| life)?|battery(?: level| percentage| status| left| life)?|"
                      r"what is the battery(?: level| percentage| status)?|how is the battery|is the battery charging|am i charging|"
                      r"is (?:my laptop|it|the laptop) charging|what is my battery(?: level)?|how much charge (?:do i have|is left))$")
_MEMORY = re.compile(r"^(?:how much (?:ram|memory)(?: do i have| is free| is left| am i using| is being used| is available| is in use|"
                     r" is used| have i got)?|(?:ram|memory) (?:usage|left|free|status)|how is the (?:memory|ram)|"
                     r"how much free (?:ram|memory)(?: do i have)?)$")
_DISK = re.compile(r"^(?:how much (?:disk |storage |drive |hard drive )?(?:space|storage)(?: do i have)?(?: is)?"
                   r"(?: left| free| available| remaining)?|how much (?:disk|storage|hard drive|drive) (?:space )?(?:is )?(?:left|free)|"
                   r"(?:free )?(?:disk|storage|drive) (?:space|usage|left)|storage left|how full is (?:my|the) (?:disk|drive|storage|"
                   r"hard drive))$")
_CPU = re.compile(r"^(?:cpu (?:usage|load)|system load|how busy is (?:the |my )?(?:cpu|computer|pc|laptop|machine)|how is the cpu|"
                  r"how loaded is (?:my |the )?(?:computer|pc|laptop|cpu)|how hard is (?:my |the )?(?:cpu|computer) working)$")
_CORES = re.compile(r"^(?:how many (?:cpu )?(?:cores|threads)(?: do i have| does (?:this|my) (?:computer|pc|laptop) have| are there)?|"
                    r"what (?:cpu|processor) do i have|which (?:cpu|processor)(?: do i have| is this)?|what is my (?:cpu|processor))$")
_TEMP = re.compile(r"^(?:how hot is (?:the |my )?(?:cpu|computer|laptop|pc|machine)|cpu temp(?:erature)?|what is the cpu temp(?:erature)?|"
                   r"how warm is (?:the |my )?(?:cpu|computer|laptop)|is (?:my |the )?(?:cpu|computer|laptop) (?:hot|overheating))$")
_UPTIME = re.compile(r"^(?:uptime|how long has (?:the |my |this )?(?:computer|pc|laptop|system|machine) been (?:on|running|up)|"
                     r"how long have i been (?:on|up)|when did i (?:last )?(?:boot|start|restart|turn on) (?:the |my )?(?:computer|pc|laptop))$")
_MY_UPTIME = re.compile(r"^how long have you been (?:running|on|up|awake|working)$")
_IP = re.compile(r"^(?:what is my (?:local |lan |private )?ip(?: address)?|my (?:local )?ip(?: address)?|what ip address am i on|"
                 r"what is this computer's ip(?: address)?)$")
_HOST = re.compile(r"^(?:what is (?:my |the |this )?(?:computer|pc|laptop|machine|host) ?name|hostname|what is this computer called|"
                   r"what is my computer called|what is the name of this computer)$")
_USER = re.compile(r"^(?:who am i logged in as|what is my (?:user ?name|login)|what user am i|whoami)$")
_OS = re.compile(r"^(?:what (?:os|operating system|distro|distribution|linux) (?:am i|are we|do i) (?:using|running|on)|what os is this|"
                 r"which (?:os|distro|linux)(?: am i (?:using|running))?|what operating system(?: is this)?|"
                 r"what is my (?:os|operating system|distro)|kernel version|which kernel(?: am i (?:using|running))?|"
                 r"what kernel version(?: am i (?:running|using))?)$")
_PLAYING = re.compile(r"^(?:what (?:song|track|music|tune) is (?:this|playing|on)|what is playing|what am i (?:listening|playing)(?: to)?|"
                      r"what is this (?:song|track)|who sings this|who is this by|who is the artist|who is singing|who sings this song|"
                      r"who is this singer|name this song|what song is this|what is currently playing|which song is this|"
                      r"what song is playing)$")


def machine_facts(q: Query) -> str | None:
    m, c = _machine(q), q.cmd
    if _BATTERY.match(c):
        b = m.battery()
        if b is None:
            return "This computer has no battery."
        percent, status = b
        tail = {"charging": " and charging", "discharging": " and running on battery", "full": " and full",
                "not charging": " and plugged in"}.get(status, "")
        return f"The battery is at {percent} percent{tail}."
    if _MEMORY.match(c):
        mem = m.memory_mb()
        return None if mem is None else f"{_gb(mem[0])} of {_gb(mem[1])} is in use, so {_gb(mem[1] - mem[0])} is free."
    if _DISK.match(c):
        disk = m.disk_gb()
        if disk is None:
            return None
        free, total = disk
        return f"{num(round(free))} GB free of {num(round(total))} GB, so {round((1 - free / total) * 100)} percent is used."
    if _CPU.match(c):
        pct = m.load_percent()
        return None if pct is None else f"The processor has been about {pct} percent busy over the last minute."
    if _CORES.match(c):
        model = m.cpu_model()
        return None if model is None else f"You have {model}, with {m.threads()} threads."
    if _TEMP.match(c):
        t = m.temperature_c()
        return "I can't read a temperature sensor on this computer." if t is None else (
            f"The hottest sensor reads {num(round(t))} degrees Celsius.")
    if _UPTIME.match(c):
        up = m.uptime_s()
        return None if up is None else f"The computer has been on for {say_duration(up)}."
    if _MY_UPTIME.match(c):
        return f"I've been running for {say_duration(time.time() - q.env.started)}."
    if _IP.match(c):
        ip = m.local_ip()
        return None if ip is None else f"Your local address is {ip}."
    if _HOST.match(c):
        return f"This computer is called {m.hostname()}."
    if _USER.match(c):
        return f"You're logged in as {m.username()}."
    if _OS.match(c):
        name = m.os_name()
        kernel = m.kernel()
        if "kernel" in c:
            return f"The kernel is {kernel}."
        return f"You're running {name}, kernel {kernel}." if name else f"The kernel is {kernel}."
    return None


def now_playing(q: Query) -> str | None:
    if q.env.now_playing is None or not _PLAYING.match(q.cmd):
        return None
    track = q.env.now_playing()
    if not track or not track.get("title"):
        return None  # nothing on this computer: it may be a song in the room, which the song-recognition tool can handle
    who = f" by {track['artist']}" if track.get("artist") else ""
    lead = "Paused: " if str(track.get("status", "")).lower() == "paused" else ""
    return f"{lead}“{track['title']}”{who}."


RULES = (
    Rule("this computer", CATEGORY, (
        "how much battery do I have", "battery level", "is the battery charging", "how much ram do I have", "memory usage",
        "how much disk space is left", "how much storage do I have left", "cpu usage", "how busy is the computer",
        "how many cpu cores do I have", "what cpu do I have", "how hot is the cpu", "how long has the computer been on", "uptime",
        "what is my ip address", "what's my computer name", "who am I logged in as", "what operating system am I using",
        "what kernel version am I running", "how long have you been running"), machine_facts),
    Rule("what is playing", CATEGORY, ("what song is this", "what's playing", "who sings this", "what is currently playing",
                                       "what am I listening to"), now_playing),
)
