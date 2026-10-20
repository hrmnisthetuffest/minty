"""Crash-loop detection and safe mode (W1-18)."""
from minty_core.config import ConfigStore
from minty_core.plugins.host import BUILTIN_PLUGIN_DIR, PluginHost
from minty_core.keystore import SecretsStore
from minty_core.safemode import SAFE_PLUGINS, BootLog


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


def test_three_unclean_starts_in_ten_minutes_mean_safe_mode(tmp_path):
    clock = Clock()
    log = BootLog(tmp_path / "boot.json", clock)
    assert log.start() is False
    clock.now += 60
    assert log.start() is False
    clock.now += 60
    assert log.start() is True  # the third start without a clean exit
    clock.now += 60
    assert log.start() is True  # and it stays on while the loop continues


def test_a_clean_exit_resets_the_count(tmp_path):
    clock = Clock()
    log = BootLog(tmp_path / "boot.json", clock)
    log.start()
    clock.now += 10
    log.start()
    log.clean_exit()
    clock.now += 10
    assert log.start() is False
    clock.now += 10
    assert log.start() is False


def test_old_crashes_do_not_count(tmp_path):
    clock = Clock()
    log = BootLog(tmp_path / "boot.json", clock)
    log.start()
    log.start()
    clock.now += 601
    assert log.start() is False


def test_clear_leaves_safe_mode(tmp_path):
    clock = Clock()
    log = BootLog(tmp_path / "boot.json", clock)
    for _ in range(3):
        log.start()
    log.clear()
    assert log.start() is False


def test_the_host_runs_only_the_safe_plugins_for_that_run(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["plugins"] = {n: {"enabled": True} for n in ("browser", "mail", "clock", "memory")}
    store.update(data)
    host = PluginHost([BUILTIN_PLUGIN_DIR], store, SecretsStore(tmp_path), tmp_path)
    host.reload()
    before = {s.name.split("_")[0] for s in host.tool_specs()}
    assert {"browser", "mail", "clock", "memory"} <= before
    host.only = set(SAFE_PLUGINS)
    names = [s.name for s in host.tool_specs()]
    assert all(any(n.startswith(p + "_") for p in SAFE_PLUGINS) for n in names)
    assert any(n.startswith("clock_") for n in names) and not any(n.startswith("browser_") for n in names)
    assert host.get_tool("browser_open") is None
    assert store.config.plugins["browser"]["enabled"] is True  # saved settings untouched


def test_leaving_safe_mode_by_voice_clears_the_loop_and_restarts(tmp_path):
    from minty_core.commands import CommandRouter
    clock = Clock()
    log = BootLog(tmp_path / "boot.json", clock)
    for _ in range(3):
        log.start()
    router = CommandRouter(agent=None, restart=True, bootlog=log)
    assert "Leaving safe mode" in router.handle("leave safe mode") and router.pending_restart
    assert log.start() is False
    assert CommandRouter(agent=None, bootlog=log).handle("leave safe mode") is None  # chat cannot restart it


def test_stats_count_starts_and_crashes_for_the_health_digest(tmp_path):
    clock = Clock()
    log = BootLog(tmp_path / "boot.json", clock)
    log.start()
    clock.now += 4000
    log.start()  # the first one never exited cleanly
    log.clean_exit()
    clock.now += 4000
    log.start()
    assert log.stats() == (3, 1)  # three starts; one earlier run ended unclean
    clock.now += 8 * 86400
    log.start()
    assert log.stats(days=7) == (1, 0)


def test_a_stop_you_asked_for_is_not_a_crash():
    """Ctrl+C (or a service stop) three times in ten minutes put Minty in safe mode, which hides most tools."""
    import asyncio

    from minty_core.safemode import is_deliberate_stop

    for stop in (None, KeyboardInterrupt(), asyncio.CancelledError(), SystemExit(0)):
        assert is_deliberate_stop(stop), stop
    for crash in (RuntimeError("boom"), OSError("gone"), SystemExit(1)):
        assert not is_deliberate_stop(crash), crash


def test_a_crash_loop_alone_no_longer_starts_safe_mode():
    """You close Minty by closing its window; that must never put it in safe mode on its own."""
    from minty_core.config import OpsConfig
    from minty_core.safemode import start_in_safe_mode

    assert OpsConfig().auto_safe_mode is False
    assert start_in_safe_mode(crash_loop=True, auto=False, requested=False) is False


def test_safe_mode_starts_when_you_ask_or_when_you_allowed_the_automatic_kind():
    from minty_core.safemode import start_in_safe_mode

    assert start_in_safe_mode(crash_loop=False, auto=False, requested=True) is True
    assert start_in_safe_mode(crash_loop=True, auto=True, requested=False) is True
    assert start_in_safe_mode(crash_loop=False, auto=True, requested=False) is False


def test_the_safe_flag_is_read_on_the_command_line_and_dropped_when_restarting():
    from minty_core.app import _without_safe_flag, parse_args

    assert parse_args(["--safe"]).safe is True and parse_args(["run", "--safe"]).safe is True
    assert parse_args([]).safe is False
    assert _without_safe_flag(["-m", "minty_core", "--safe", "run", "--no-panel"]) == ["-m", "minty_core", "run", "--no-panel"]


def test_entering_safe_mode_by_voice_restarts_into_it_once(tmp_path):
    from minty_core.commands import CommandRouter

    log = BootLog(tmp_path / "boot.json", Clock())
    router = CommandRouter(agent=None, restart=True, bootlog=log)
    assert "safe mode" in router.handle("enter safe mode").lower() and router.pending_restart
    assert log.take_safe_request() is True  # the next start is a safe one
    assert log.take_safe_request() is False  # and only that one
    assert CommandRouter(agent=None, bootlog=log).handle("restart in safe mode") is None  # chat cannot restart it
