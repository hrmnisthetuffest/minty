"""Reloading plugins (every settings change does) must not leak file descriptors (found by the soak, W1B-11)."""
import os

from tests.plugin_helpers import make_host


def open_files() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_reloading_the_plugins_ten_times_does_not_leave_databases_open(tmp_path):
    host = make_host(tmp_path / "cfg", plugins={"research": {"enabled": True, "folder": str(tmp_path / "ws")},
                                                  "school": {"enabled": True}})
    host.reload()  # warm: the first load opens whatever it needs once
    before = open_files()
    for _ in range(10):
        host.reload()
    assert open_files() - before <= 1


def test_a_changed_workspace_folder_gets_its_own_library_and_the_old_one_is_closed(tmp_path):
    host = make_host(tmp_path / "cfg", plugins={"research": {"enabled": True, "folder": str(tmp_path / "a")}})
    first = host._plugins["research"]
    assert first.error is None
    data = host._store.config.model_dump(mode="json")
    data["plugins"]["research"]["folder"] = str(tmp_path / "b")
    host._store.update(data)  # the listener reloads
    host.reload()
    assert host._plugins["research"].error is None
    assert (tmp_path / "b" / "library").exists()


def test_the_work_plugins_keep_one_database_open_across_reloads(tmp_path):
    """Attention, recipes, the studio and the outbox each keep a database; every settings change reloads the plugins."""
    host = make_host(tmp_path / "cfg", plugins={"outbox": {"enabled": True}, "studio": {"enabled": True},
                                                  "recipes": {"enabled": True}, "attention": {"enabled": True}})
    host.reload()
    before = open_files()
    for _ in range(10):
        host.reload()
    assert open_files() - before <= 1


def test_after_a_reload_the_new_settings_reach_the_plugins_that_keep_their_database(tmp_path):
    host = make_host(tmp_path / "cfg", plugins={"recipes": {"enabled": True, "catch_up_minutes": 5}})
    first = host._services["recipes"]
    data = host._store.config.model_dump(mode="json")
    data["plugins"]["recipes"]["catch_up_minutes"] = 90
    host._store.update(data)
    host.reload()
    assert host._services["recipes"] is first and first._catch_up == 90  # the same open store, the new setting
