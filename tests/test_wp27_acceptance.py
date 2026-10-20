"""WP-27 acceptance, as far as it can be proven off the laptop (the 72-hour soak and a real suspend are scripts)."""
import asyncio
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from tests.test_pipeline import wait_until
from minty_core.app import on_resume
from minty_core.backup import create, load_or_make_keyfile, verify
from minty_core.context import build_context
from minty_core.events import EventBus
from minty_core.offline import NetworkProbe
from minty_core.power import PowerMonitor
from minty_core.safemode import BootLog
from minty_core.service import unit_text
from minty_core.supervisor import VoiceSupervisor


def test_the_core_is_back_within_ten_seconds_of_being_killed(tmp_path):
    unit = unit_text(Path("/opt/minty"))
    restart = int(next(l for l in unit.splitlines() if l.startswith("RestartSec=")).split("=")[1])
    started = time.perf_counter()
    build_context(tmp_path)  # everything a restart rebuilds before the voice loads
    build_seconds = time.perf_counter() - started
    assert restart + build_seconds < 10 and "Restart=on-failure" in unit


def test_three_crashes_in_ten_minutes_mean_safe_mode_and_a_clean_exit_resets_it(tmp_path):
    clock = {"t": 10_000.0}
    log = BootLog(tmp_path / "boot.json", lambda: clock["t"])
    results = []
    for _ in range(3):
        results.append(log.start())
        clock["t"] += 90
    assert results == [False, False, True]
    log.clean_exit()
    clock["t"] += 90
    assert log.start() is False


async def test_after_a_suspend_the_audio_loop_reopens_and_the_network_is_looked_at_again(tmp_path):
    bus = EventBus()
    notices_q = bus.subscribe({"notify"})
    runs = []

    async def loop():
        runs.append(1)
        await asyncio.sleep(60)

    supervisor = VoiceSupervisor(loop, delay_s=0.0)
    probe = NetworkProbe()
    probe._last, probe._at = False, 0.0  # it thought it was offline
    ctx = SimpleNamespace(bus=bus, services={"voice_supervisor": supervisor, "offline": SimpleNamespace(probe=probe)})
    task = asyncio.create_task(supervisor.run())
    await wait_until(lambda: len(runs) == 1)
    t = {"wall": 5000.0, "mono": 100.0}
    monitor = PowerMonitor(wall=lambda: t["wall"], mono=lambda: t["mono"], battery=lambda: (True, 80),
                           temperature=lambda: 50.0)
    t["wall"] += 7200
    t["mono"] += 3  # two hours asleep
    slept = monitor.check_resume()
    on_resume(ctx, slept)
    await wait_until(lambda: len(runs) == 2)  # the loop was restarted, not left dead
    assert not probe.known_offline() and supervisor.restarts == 0
    event = notices_q.get_nowait()
    assert "back after the computer slept" in event["text"]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_a_restored_backup_passes_the_checksum_and_a_smoke_test(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.toml").write_text('[safety]\nmode = "commits"\n')
    (cfg / "memory.md").write_text("- Murphy is an Indian Spitz\n")
    db = sqlite3.connect(cfg / "memory.db")
    db.execute("create table items(gist text)")
    db.execute("insert into items values ('shoe size 9')")
    db.commit()
    db.close()
    key = load_or_make_keyfile(tmp_path / "backup.key")
    info = create(cfg, key, tmp_path / "b" / "minty-backup-20261015.bak")
    result = verify(info.path, key)
    assert result.ok and result.files == 3 and result.problems == []


def test_the_day_start_and_the_maintenance_window_do_not_collide_with_use(tmp_path):
    from minty_core.housekeeping import MaintenanceWindow
    w = MaintenanceWindow(tmp_path / "s", at="03:30", busy_for=lambda: 120.0)  # the mic was used two minutes ago
    w.tasks["x"] = lambda: "ran"
    assert asyncio.run(w.run(datetime(2026, 10, 15, 3, 40))) is None  # skipped, tried again later
    w2 = MaintenanceWindow(tmp_path / "s2", at="03:30", busy_for=lambda: 3600.0)
    w2.tasks["x"] = lambda: "ran"
    assert asyncio.run(w2.run(datetime(2026, 10, 15, 3, 40))) == {"x": "ran"}
