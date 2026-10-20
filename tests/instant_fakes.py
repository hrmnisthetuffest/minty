"""Fakes for the instant-answer tests: a fixed clock (a Tuesday afternoon in India), a seeded dice, and a computer described by files."""
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minty_core.instant import Env, Instant
from minty_core.instant.system import Machine

IST = timezone(timedelta(hours=5, minutes=30), "IST")
NOW = datetime(2026, 10, 20, 16, 32, 5, tzinfo=IST)  # Tuesday 20 October 2026


def machine(root: Path, **kw) -> Machine:
    def put(name: str, text: str) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    put("proc/meminfo", "MemTotal:       16777216 kB\nMemFree:         2000000 kB\nMemAvailable:   11534336 kB\n")
    put("proc/cpuinfo", "processor\t: 0\nmodel name\t: Test CPU 3000\nprocessor\t: 1\nprocessor\t: 2\nprocessor\t: 3\n")
    put("proc/loadavg", "1.00 0.80 0.50 1/100 1234\n")
    put("proc/uptime", "11520.50 40000.00\n")
    put("sys/class/power_supply/BAT0/capacity", "87\n")
    put("sys/class/power_supply/BAT0/status", "Discharging\n")
    put("sys/class/thermal/thermal_zone0/temp", "52000\n")
    put("sys/class/thermal/thermal_zone1/temp", "47000\n")
    put("etc/os-release", 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\n')
    defaults = dict(root=root, home=root, hostname=lambda: "laptop", username=lambda: "alex", kernel=lambda: "7.2.11-arch1-1",
                    local_ip=lambda: "192.168.1.12", disk_usage=lambda p: (500 * 10**9, 360 * 10**9, 140 * 10**9))
    return Machine(**{**defaults, **kw})


def engine(root: Path | None = None, now: datetime = NOW, **kw) -> Instant:
    env = dict(now=lambda: now, rng=random.Random(7), wake_words=("minty", "mindy"), version="1.0.0-alpha.20",
               last_reply=lambda: "It is raining.", last_request=lambda: "Is it raining?",
               now_playing=lambda: {"title": "Astronaut in the Ocean", "artist": "Masked Wolf", "status": "Playing"})
    if root is not None:
        env["system"] = machine(root)
    skip = kw.pop("skip", ())
    return Instant(Env(**{**env, **kw}), skip=skip)


def say(eng: Instant, text: str) -> str | None:
    hit = eng.answer(text)
    return hit.reply if hit else None
