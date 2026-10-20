"""Knowing the machine: what it has, which tier it is, and which settings suit it. The probe reads /proc and /sys under a root
folder, so every machine in these tests is a few files in a temporary directory."""
from pathlib import Path

import pytest

from minty_core.hwprobe import Hardware, Gpu, probe, recommend, tier

MODERN_FLAGS = "fpu sse4_2 avx avx2 fma bmi2"
OLD_FLAGS = "fpu sse sse2 ssse3 sse4_1 sse4_2 avx"  # a 2012 laptop: AVX but no AVX2


def machine(tmp_path: Path, *, model="Test CPU", flags=MODERN_FLAGS, threads=8, ram_kb=16_000_000, swap_kb=2_000_000,
            available_kb=None, rotational=None, gpus=(), battery=None) -> Path:
    root = tmp_path / "root"
    (root / "proc").mkdir(parents=True)
    cpuinfo = "".join(f"processor\t: {i}\nmodel name\t: {model}\nflags\t\t: {flags}\n\n" for i in range(threads))
    (root / "proc/cpuinfo").write_text(cpuinfo)
    (root / "proc/meminfo").write_text(f"MemTotal: {ram_kb} kB\nMemAvailable: {available_kb or ram_kb // 2} kB\n"
                                       f"SwapTotal: {swap_kb} kB\n")
    if rotational is not None:
        (root / "sys/block/sda/queue").mkdir(parents=True)
        (root / "sys/block/sda/queue/rotational").write_text("1" if rotational else "0")
    for i, (vendor, vram) in enumerate(gpus):
        d = root / f"sys/class/drm/card{i}/device"
        d.mkdir(parents=True)
        (d / "vendor").write_text(vendor + "\n")
        if vram:
            (d / "mem_info_vram_total").write_text(str(vram * 1024 * 1024))
    if battery is not None:
        d = root / "sys/class/power_supply/BAT0"
        d.mkdir(parents=True)
        (d / "status").write_text(battery + "\n")
    return root


def test_the_probe_reads_cpu_memory_disk_gpu_and_battery(tmp_path):
    root = machine(tmp_path, model="Fast CPU", threads=12, ram_kb=16_000_000, rotational=False,
                   gpus=[("0x10de", None)], battery="Discharging")
    hw = probe(root, run=lambda cmd: "")
    assert hw.cpu_model == "Fast CPU" and hw.threads == 12 and {"avx", "avx2"} <= hw.flags
    assert hw.ram_mb == 16_000_000 // 1024 and hw.swap_mb == 2_000_000 // 1024 and hw.available_mb == 8_000_000 // 1024
    assert hw.rotational is False and hw.on_battery is True
    assert hw.gpus == (Gpu("nvidia", "NVIDIA GPU", None),)


def test_nvidia_smi_gives_the_gpu_a_name_and_memory(tmp_path):
    root = machine(tmp_path, gpus=[("0x10de", None)])
    hw = probe(root, run=lambda cmd: "NVIDIA GeForce GTX 1650, 4096\n" if cmd[0] == "nvidia-smi" else "")
    assert hw.gpus == (Gpu("nvidia", "NVIDIA GeForce GTX 1650", 4096),)


def test_amd_and_intel_graphics_are_recognised_and_amd_reports_its_memory(tmp_path):
    root = machine(tmp_path, gpus=[("0x1002", 8192), ("0x8086", None)])
    kinds = {g.kind: g for g in probe(root, run=lambda cmd: "").gpus}
    assert kinds["amd"].vram_mb == 8192 and "intel" in kinds


def test_a_machine_with_unreadable_files_is_still_described(tmp_path):
    hw = probe(tmp_path / "nothing-here", run=lambda cmd: "")
    assert isinstance(hw, Hardware) and hw.ram_mb == 0 and hw.threads >= 1 and hw.rotational is None and hw.gpus == ()
    assert tier(hw) == "t1"  # not knowing is not a reason to turn things off


@pytest.mark.parametrize("kw, expected", [
    (dict(ram_kb=3_800_000, threads=2, flags=OLD_FLAGS), "t0"),  # the old laptop with 4 GB
    (dict(ram_kb=8_000_000, threads=4, flags=OLD_FLAGS), "t1"),
    (dict(ram_kb=16_000_000, threads=4, flags=OLD_FLAGS), "t1"),  # lots of memory but no AVX2 and slow cores
    (dict(ram_kb=8_000_000, threads=8, flags=MODERN_FLAGS), "t1"),
    (dict(ram_kb=16_000_000, threads=12, flags=MODERN_FLAGS), "t2"),
    (dict(ram_kb=32_000_000, threads=16, flags=MODERN_FLAGS), "t2"),
])
def test_the_tier_follows_memory_cores_and_instruction_set(tmp_path, kw, expected):
    assert tier(probe(machine(tmp_path, **kw), run=lambda cmd: "")) == expected


def test_an_arm_board_is_judged_on_memory_and_cores_not_on_missing_x86_flags(tmp_path):
    root = machine(tmp_path, flags="fp asimd aes crc32", threads=4, ram_kb=8_000_000)
    assert tier(probe(root, run=lambda cmd: "")) == "t1"


def test_by_default_nothing_visible_changes_on_any_computer_and_the_notes_say_why(tmp_path):
    """Minty looks and works the same everywhere; a slow computer is told about the lite profile, not given it."""
    old = probe(machine(tmp_path / "old", ram_kb=3_800_000, threads=2, flags=OLD_FLAGS, rotational=True), run=lambda cmd: "")
    plan = recommend(old)
    assert plan.changes == {} and plan.tier == "t0"
    assert any("same" in n and "lite" in n for n in plan.notes) and any("AVX2" in n for n in plan.notes)
    assert any("spinning disk" in n for n in plan.notes)
    modern = probe(machine(tmp_path / "new", ram_kb=32_000_000, threads=16, rotational=False), run=lambda cmd: "")
    plan = recommend(modern)
    assert plan.changes == {} and any("nothing needs to be turned down" in n for n in plan.notes)


def test_the_lite_profile_is_what_the_old_default_was_and_is_never_chosen_for_you(tmp_path):
    hw = probe(machine(tmp_path, ram_kb=3_800_000, threads=2, flags=OLD_FLAGS), run=lambda cmd: "")
    plan = recommend(hw, lite=True)
    c = plan.changes
    assert c == {"tts.engine": "piper", "audio.live_captions": False, "terminal_reduced_motion": True,
                 "terminal_images": "off", "stt.local_model": "tiny.en"}
    assert any("Lite" in n and "still works" in n for n in plan.notes)


def test_a_gpu_is_mentioned_but_changes_nothing(tmp_path):
    hw = probe(machine(tmp_path, ram_kb=32_000_000, threads=16, gpus=[("0x10de", None)]),
               run=lambda cmd: "NVIDIA GeForce RTX 3060, 12288\n" if cmd[0] == "nvidia-smi" else "")
    plan = recommend(hw)
    assert plan.changes == {} and any("GPU" in n for n in plan.notes)


def test_every_lite_setting_is_a_real_setting_with_a_valid_value(tmp_path):
    from minty_core.config import Config

    hw = probe(machine(tmp_path, ram_kb=3_800_000, threads=2, flags=OLD_FLAGS), run=lambda cmd: "")
    data = Config().model_dump(mode="json")
    for dotted, value in recommend(hw, lite=True).changes.items():
        node = data
        *path, last = dotted.split(".")
        for part in path:
            node = node[part]
        assert last in node, dotted  # the setting exists
        node[last] = value
    Config.model_validate(data)  # and the value is allowed
