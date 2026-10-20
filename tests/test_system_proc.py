"""122 / 645: load, memory and per-process CPU straight from /proc, the heavy-work gate and the process explainer."""
import os

import pytest

from minty_core.system.osinfo import OS
from minty_core.system.proc import (CpuSampler, ResourceGate, explain_process, loadavg, memory, owner_package,
                                    read_procs, top_cpu)


def make_proc(root, pid, name, ppid=1, utime=0, stime=0, rss_pages=100, start=1000, cmdline=None, uid=1000, exe=None):
    d = root / str(pid)
    d.mkdir(parents=True)
    fields = ["S", str(ppid)] + ["0"] * 9 + [str(utime), str(stime), "0", "0", "20", "0", "1", "0", str(start), "0",
                                              str(rss_pages)]
    (d / "stat").write_text(f"{pid} ({name}) " + " ".join(fields) + "\n")
    (d / "cmdline").write_bytes(("\0".join(cmdline or [name]) + "\0").encode() if cmdline != [] else b"")
    (d / "status").write_text(f"Name:\t{name}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    if exe:
        os.symlink(exe, d / "exe")


@pytest.fixture
def proc(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    (root / "loadavg").write_text("0.52 0.40 0.30 1/200 1234\n")
    (root / "meminfo").write_text("MemTotal:       16000000 kB\nMemFree: 1000 kB\nMemAvailable:    8000000 kB\n")
    (root / "uptime").write_text("100000.00 200000.00\n")
    return root


def test_load_and_memory(proc):
    assert loadavg(proc) == (0.52, 0.40, 0.30)
    assert memory(proc) == (16000000, 8000000)
    assert loadavg(proc / "nope") == (0.0, 0.0, 0.0) and memory(proc / "nope") == (0, 0)


def test_processes_are_read_even_with_parentheses_in_the_name(proc):
    make_proc(proc, 10, "Web Content (x)", ppid=2, utime=50, stime=25, rss_pages=2000)
    make_proc(proc, 11, "kthreadd", ppid=0, cmdline=[])
    (proc / "self").mkdir()  # not a pid
    by = {p.pid: p for p in read_procs(proc, page_kb=4)}
    assert by[10].name == "Web Content (x)" and by[10].ticks == 75 and by[10].rss_kb == 8000 and by[10].ppid == 2
    assert by[11].cmdline == [] and set(by) == {10, 11}


def test_a_vanished_or_unreadable_process_is_skipped(proc):
    make_proc(proc, 10, "ok")
    (proc / "99").mkdir()  # no stat file: it exited while we looked
    assert [p.pid for p in read_procs(proc)] == [10]


def test_cpu_percent_is_the_change_between_two_samples(proc):
    make_proc(proc, 10, "busy", utime=0)
    make_proc(proc, 11, "idle", utime=0)
    t = [0.0]
    s = CpuSampler(read=lambda: read_procs(proc), clock=lambda: t[0], clk_tck=100, cpus=4)
    assert all(pct == 0 for _p, pct in s.sample())  # nothing to compare yet
    make_proc_update(proc, 10, utime=150)  # 1.5 s of CPU in 2 s -> 75 % of one core
    t[0] = 2.0
    rows = dict((p.pid, pct) for p, pct in s.sample())
    assert rows[10] == pytest.approx(75.0) and rows[11] == 0
    assert [p.pid for p, _ in top_cpu([(p, rows[p.pid]) for p in read_procs(proc)], 1)] == [10]


def make_proc_update(root, pid, utime):
    stat = (root / str(pid) / "stat")
    head, _, tail = stat.read_text().rpartition(") ")
    parts = tail.split()
    parts[11] = str(utime)
    stat.write_text(head + ") " + " ".join(parts) + "\n")


def test_the_gate_says_why_it_is_not_a_good_time(proc):
    calm = ResourceGate(load=lambda: (0.5, 0.5, 0.5), mem=lambda: (16000, 8000), cpus=lambda: 4,
                        power=lambda: (True, 90, False))
    assert calm.check().ok
    busy = ResourceGate(load=lambda: (7.0, 3.0, 2.0), mem=lambda: (16000, 8000), cpus=lambda: 4,
                        power=lambda: (True, 90, False))
    d = busy.check()
    assert not d.ok and "busy" in d.reason
    low_mem = ResourceGate(load=lambda: (0.1, 0, 0), mem=lambda: (16000, 1000), cpus=lambda: 4, power=lambda: (True, 90, False))
    assert not low_mem.check().ok and "memory" in low_mem.check().reason
    battery = ResourceGate(load=lambda: (0.1, 0, 0), mem=lambda: (16000, 8000), cpus=lambda: 4,
                           power=lambda: (False, 25, False))
    assert not battery.check().ok and "battery" in battery.check().reason
    hot = ResourceGate(load=lambda: (0.1, 0, 0), mem=lambda: (16000, 8000), cpus=lambda: 4, power=lambda: (True, 90, True))
    assert not hot.check().ok and "hot" in hot.check().reason


def test_the_explainer_says_what_a_process_is_in_plain_words(proc):
    make_proc(proc, 10, "pipewire", ppid=1, utime=5000, rss_pages=2500, start=100, exe="/usr/bin/pipewire")
    make_proc(proc, 20, "mystery", ppid=10, rss_pages=100, start=90000, cmdline=["/home/h/mystery", "--go"],
              exe="/home/h/mystery")
    make_proc(proc, 30, "python", ppid=1, cmdline=["python", "-m", "minty_core"], exe="/usr/bin/python3.13")
    procs = read_procs(proc, page_kb=4)
    text = explain_process(procs, "pipewire", uptime_s=100000, clk_tck=100)
    assert "pipewire" in text and "sound" in text.lower() and "pid 10" in text
    unknown = explain_process(procs, "mystery", uptime_s=100000, clk_tck=100)
    assert "I don't know this program" in unknown and "/home/h/mystery" in unknown and "started by pipewire" in unknown
    assert "kill" in unknown and "systemd" not in unknown.split("kill")[0]
    assert "That is me" in explain_process(procs, "30", uptime_s=100000, clk_tck=100)
    assert "No running program" in explain_process(procs, "nosuch", uptime_s=1, clk_tck=100)


def test_do_not_end_core_programs_advice(proc):
    make_proc(proc, 1, "systemd", ppid=0, exe="/usr/lib/systemd/systemd")
    text = explain_process(read_procs(proc), "systemd", uptime_s=1000, clk_tck=100)
    assert "Do not end" in text


async def test_the_owning_package_comes_from_the_right_package_manager():
    calls = []

    class Fake:
        async def run(self, argv, timeout=None):
            calls.append(argv)
            from minty_core.system.runner import Result
            return Result(0, "/usr/bin/pipewire is owned by pipewire 1.2.7-1\n" if argv[0] == "pacman"
                          else "pipewire: /usr/bin/pipewire\n")

    assert await owner_package(OS("arch", "Arch", "pacman"), Fake(), "/usr/bin/pipewire") == "pipewire"
    assert await owner_package(OS("linuxmint", "Mint", "apt"), Fake(), "/usr/bin/pipewire") == "pipewire"
    assert [c[0] for c in calls] == ["pacman", "dpkg"]
    assert await owner_package(OS(), Fake(), "/usr/bin/pipewire") == ""
