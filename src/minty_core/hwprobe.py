"""What this computer is, which of three tiers it belongs to, and the settings that suit it (docs/performance-plan.md, workstream A).

The probe reads /proc and /sys under a root folder (so a test can describe any machine with a few files) and asks `nvidia-smi` about
an NVIDIA card when there is one. It never installs anything and never changes anything; `recommend` only says what it would set,
and `minty tune` applies that after you agree. Not knowing something is never a reason to switch a feature off: a missing file gives
the middle tier.

Tiers: **t0** very old or tiny (under about 5.5 GB of memory), **t1** an ordinary laptop or a machine that lacks something (under 12
GB, fewer than 8 threads, or no AVX2 on x86), **t2** modern (12 GB or more, 8 or more threads, and AVX2 on x86).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

T0_MAX_RAM_MB = 5500
T2_MIN_RAM_MB = 12000
T2_MIN_THREADS = 8
VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
GPU_NAMES = {"nvidia": "NVIDIA GPU", "amd": "AMD GPU", "intel": "Intel graphics", "other": "graphics card"}


@dataclass(frozen=True)
class Gpu:
    kind: str  # nvidia | amd | intel | other
    name: str
    vram_mb: int | None


@dataclass(frozen=True)
class Hardware:
    cpu_model: str
    threads: int
    flags: frozenset[str]
    ram_mb: int
    available_mb: int
    swap_mb: int
    rotational: bool | None  # a spinning disk; None when it could not be told
    gpus: tuple[Gpu, ...] = ()
    on_battery: bool | None = None

    @property
    def x86(self) -> bool:
        return bool(self.flags & {"sse2", "sse4_2", "avx", "avx2"})

    @property
    def avx2(self) -> bool:
        return "avx2" in self.flags


@dataclass(frozen=True)
class Plan:
    tier: str
    changes: dict[str, object] = field(default_factory=dict)  # dotted setting name -> value
    notes: list[str] = field(default_factory=list)


def _run(cmd: list[str]) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
        return done.stdout if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _kb(text: str, key: str) -> int:
    for line in text.splitlines():
        if line.startswith(key + ":"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return 0
    return 0


def probe(root: Path = Path("/"), run: Callable[[list[str]], str] = _run) -> Hardware:
    root = Path(root)
    cpu = _read(root / "proc/cpuinfo")
    model, flags, processors = "unknown", frozenset(), 0
    for line in cpu.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "processor":
            processors += 1
        elif key in ("model name", "hardware", "model") and model == "unknown" and value:
            model = value
        elif key in ("flags", "features") and not flags:
            flags = frozenset(value.split())
    mem = _read(root / "proc/meminfo")
    return Hardware(cpu_model=model, threads=processors or os.cpu_count() or 1, flags=flags,
                    ram_mb=_kb(mem, "MemTotal") // 1024, available_mb=_kb(mem, "MemAvailable") // 1024,
                    swap_mb=_kb(mem, "SwapTotal") // 1024, rotational=_rotational(root), gpus=_gpus(root, run),
                    on_battery=_battery(root))


def _rotational(root: Path) -> bool | None:
    for dev in sorted((root / "sys/block").glob("*")) if (root / "sys/block").is_dir() else []:
        if dev.name.startswith(("sd", "nvme", "vd", "hd", "mmcblk")):
            value = _read(dev / "queue/rotational").strip()
            if value in ("0", "1"):
                return value == "1"
    return None


def _gpus(root: Path, run: Callable[[list[str]], str]) -> tuple[Gpu, ...]:
    found: list[Gpu] = []
    drm = root / "sys/class/drm"
    for card in sorted(drm.glob("card[0-9]")) if drm.is_dir() else []:
        kind = VENDORS.get(_read(card / "device/vendor").strip().lower(), "other")
        vram = _read(card / "device/mem_info_vram_total").strip()
        found.append(Gpu(kind, GPU_NAMES[kind], int(vram) // (1024 * 1024) if vram.isdigit() else None))
    if any(g.kind == "nvidia" for g in found):
        out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]).strip().splitlines()
        if out:
            name, _, mem = out[0].rpartition(",")
            found = [Gpu("nvidia", name.strip() or GPU_NAMES["nvidia"], int(mem) if mem.strip().isdigit() else None)
                     if g.kind == "nvidia" and g is next(x for x in found if x.kind == "nvidia") else g for g in found]
    return tuple(found)


def _battery(root: Path) -> bool | None:
    states = [_read(p / "status").strip() for p in sorted((root / "sys/class/power_supply").glob("BAT*"))] \
        if (root / "sys/class/power_supply").is_dir() else []
    if not states:
        return None
    return any(s == "Discharging" for s in states)


def tier(hw: Hardware) -> str:
    if hw.ram_mb == 0:  # unknown: do not turn things off on a guess
        return "t1"
    if hw.ram_mb < T0_MAX_RAM_MB:
        return "t0"
    if hw.ram_mb >= T2_MIN_RAM_MB and hw.threads >= T2_MIN_THREADS and (hw.avx2 or not hw.x86):
        return "t2"
    return "t1"


def _gpu_helps_speech(hw: Hardware) -> bool:
    return any((g.kind == "nvidia" and (g.vram_mb or 0) >= 4000) or (g.kind == "amd" and (g.vram_mb or 0) >= 6000)
               for g in hw.gpus)


def lite_changes(hw: Hardware) -> dict[str, object]:
    """The settings that trade looks and features for speed, for a computer that struggles. Never applied unless asked for."""
    return {"tts.engine": "piper", "audio.live_captions": False, "terminal_reduced_motion": True, "terminal_images": "off",
            "stt.local_model": "tiny.en"}


def recommend(hw: Hardware, lite: bool = False) -> Plan:
    """What `minty tune` says about this computer. By default it changes nothing: Minty looks and works the same everywhere and
    adapts its speed by itself (the display slows its redraws when a frame is expensive, captions wait for the recogniser).
    `lite=True` is the choice you make when it still feels slow: the light voice and recogniser, no live captions, no animation, no
    terminal pictures."""
    t = tier(hw)
    gb = hw.ram_mb / 1024
    notes: list[str] = []
    if lite:
        notes.append("Lite profile: the light voice and recogniser, no live captions, a still display and no terminal pictures. "
                     "Everything still works; it just looks and sounds simpler.")
        return Plan(t, lite_changes(hw), notes)
    if t == "t0":
        notes.append(f"{gb:.1f} GB of memory and {hw.threads} thread(s): Minty works the same here and loads voice models only when "
                     "needed. If it feels slow, `minty tune --lite` trades some looks for speed.")
    if hw.x86 and not hw.avx2:
        notes.append("No AVX2 on this processor: the Kokoro voice and the larger local recognisers are slow here. Cloud recognition "
                     "and the Piper voice (`tts.engine = \"piper\"`) are much faster if you want them.")
    if _gpu_helps_speech(hw):
        notes.append("A GPU with enough memory was found: the larger local recogniser (small.en) is worth using with it.")
    if hw.rotational:
        notes.append("This computer has a spinning disk: the first start and the first use of each voice model are slow; later "
                     "ones are faster.")
    if hw.on_battery:
        notes.append("Running on battery: expect slower local models.")
    if t == "t2":
        notes.append("A modern computer: nothing needs to be turned down.")
    return Plan(t, {}, notes)
