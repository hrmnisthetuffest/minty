"""Leak guard and a short soak (W1B-11)."""
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

from minty_core.leakguard import LeakGuard, Sample, fitted_change, take

ROOT = Path(__file__).resolve().parents[1]


def series(rss=lambda i: 100.0, fds=lambda i: 20, threads=lambda i: 8, wal=lambda i: 1.0, queue=lambda i: 0, n=60):
    g = LeakGuard()
    for i in range(n):
        g.add(Sample(float(i), rss(i), fds(i), threads(i), wal(i), queue(i)))
    return g


def test_a_flat_series_with_noise_is_quiet():
    g = series(rss=lambda i: 100 + (i % 5) * 0.4, fds=lambda i: 20 + (i % 3))
    assert g.alarms() == []


def test_startup_growth_is_ignored_but_a_steady_climb_is_not():
    warm = series(rss=lambda i: 100 + min(i, 10) * 6.0)  # grows early, then settles
    assert warm.alarms() == []
    leak = series(rss=lambda i: 100 + i * 1.5)
    assert any("memory is growing" in a for a in leak.alarms())


def test_descriptors_threads_wal_and_queue_each_alarm():
    assert any("open files" in a for a in series(fds=lambda i: 20 + i // 2).alarms())
    assert any("threads are climbing" in a for a in series(threads=lambda i: 8 + i // 8).alarms())
    assert any("database log" in a for a in series(wal=lambda i: 80.0).alarms())
    assert any("waiting in a queue" in a for a in series(queue=lambda i: 150).alarms())


def test_too_few_samples_never_alarm_and_a_spike_is_not_a_trend():
    assert series(rss=lambda i: 100 + i * 10, n=6).alarms() == []
    spike = series(rss=lambda i: 400.0 if i == 40 else 100.0)
    assert spike.alarms() == []
    assert fitted_change([1.0, 2.0, 3.0, 4.0])[0] == 3.0


def test_the_live_sampler_reads_this_process(tmp_path):
    (tmp_path / "x.sqlite-wal").write_bytes(b"x" * 2 * 2**20)
    s = take(tmp_path, queue=3)
    assert s.rss_mb > 10 and s.fds > 3 and s.threads >= 1 and 1.9 < s.wal_mb < 2.1 and s.queue == 3


def test_a_short_soak_of_the_bookkeeping_is_bounded():
    """Three seconds of synthetic use (jobs, approvals, receipts, journal, traces, config): no leak alarm."""
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "soak.py"), "--minutes", "0.06",
                          "--sample-every", "0.1"], capture_output=True, text=True,
                         env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "cycles; memory" in out.stdout and "ALARM" not in out.stdout
    assert asyncio.get_event_loop_policy() is not None and tempfile.gettempdir()
