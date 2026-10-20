"""Disk guard and the maintenance window (W1B-01)."""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from minty_core.housekeeping import DiskGuard, MaintenanceWindow, maintain_databases


def make_tree(root: Path):
    for folder in ("traces", "history/raw", "history/days", "journal-archive", "config-snapshots"):
        (root / folder).mkdir(parents=True)
    for i in range(5):
        f = root / "traces" / f"2026-10-0{i + 1}.jsonl"
        f.write_bytes(b"x" * 400_000)
        os.utime(f, (1_700_000_000 + i * 1000, 1_700_000_000 + i * 1000))
    (root / "history/raw/2026-10-01.jsonl").write_bytes(b"precious" * 100_000)
    (root / "secrets.toml").write_text('groq = "gsk-not-real"')
    (root / "memory.md").write_text("- a fact\n")
    (root / "journal-archive/a.json").write_bytes(b"y" * 300_000)


def test_it_trims_only_reclaimable_files_oldest_first_until_under_budget(tmp_path):
    make_tree(tmp_path)
    free = {"mb": 5000}
    guard = DiskGuard(tmp_path, free_floor_mb=1024, max_config_mb=2.4, disk_usage=lambda p: (0, 0, free["mb"] * 2**20))
    before = guard.measure()
    assert before.over_budget and not before.low_on_space
    removed = guard.trim()
    assert removed[0] == "traces/2026-10-01.jsonl"  # the oldest first
    trace_names = [r for r in removed if r.startswith("traces/")]
    assert trace_names == sorted(trace_names)
    assert removed and all(r.startswith(("traces/", "journal-archive/")) for r in removed)
    assert (tmp_path / "history/raw/2026-10-01.jsonl").exists() and (tmp_path / "secrets.toml").exists()
    assert (tmp_path / "memory.md").exists()
    assert not guard.measure().over_budget


def test_low_free_space_triggers_a_trim_and_nothing_happens_when_all_is_well(tmp_path):
    make_tree(tmp_path)
    ok = DiskGuard(tmp_path, free_floor_mb=100, max_config_mb=9999, disk_usage=lambda p: (0, 0, 500 * 2**20))
    assert ok.trim() == []
    low = DiskGuard(tmp_path, free_floor_mb=100, max_config_mb=9999, disk_usage=lambda p: (0, 0, 10 * 2**20))
    assert low.measure().low_on_space and low.trim()  # it cannot reach the floor, so it removes everything reclaimable
    assert (tmp_path / "history/raw/2026-10-01.jsonl").exists()


def test_databases_are_compacted(tmp_path):
    db = sqlite3.connect(tmp_path / "memory.db")
    db.execute("create table t (x text)")
    db.executemany("insert into t values (?)", [("x" * 1000,)] * 3000)
    db.commit()
    db.execute("delete from t")
    db.commit()
    db.close()
    before = (tmp_path / "memory.db").stat().st_size
    out = maintain_databases(tmp_path)
    assert (tmp_path / "memory.db").stat().st_size < before and out[0].startswith("memory.db:")


async def test_the_window_runs_once_a_day_and_only_when_quiet(tmp_path):
    busy = {"for": 5.0}  # seconds since the microphone was used
    ran = []
    w = MaintenanceWindow(tmp_path / "state", at="03:30", busy_for=lambda: busy["for"])
    w.tasks["trim"] = lambda: ran.append("trim") or "removed 2"

    async def vacuum():
        ran.append("vacuum")
        return ["a: ok"]

    w.tasks["vacuum"] = vacuum
    w.tasks["broken"] = lambda: 1 / 0
    at = datetime(2026, 10, 14, 3, 40)
    assert await w.run(datetime(2026, 10, 14, 12, 0)) is None  # not the window
    assert await w.run(at) is None and ran == []  # the microphone was used five seconds ago
    busy["for"] = 3600.0
    out = await w.run(at)
    assert out == {"trim": "removed 2", "vacuum": "a: ok", "broken": "failed: ZeroDivisionError"} and ran == ["trim", "vacuum"]
    assert await w.run(datetime(2026, 10, 14, 3, 50)) is None  # already done today
    assert await w.run(datetime(2026, 10, 15, 3, 35)) is not None  # tomorrow again
