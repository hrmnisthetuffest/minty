"""One Minty per settings folder. A second `minty` used to load the voice models, start a second Telegram poller on the same bot and open the
microphone, and only then died on the busy port with a traceback (leaving the terminal in mouse-reporting mode). It now says so at once."""
import fcntl
import os
import signal
import stat
import subprocess
import sys
import time

import pytest

from minty_core import app, instance


def test_the_second_start_is_refused_and_names_the_first(tmp_path):
    first = instance.acquire(tmp_path)
    try:
        with pytest.raises(instance.AlreadyRunning) as caught:
            instance.acquire(tmp_path)
        assert caught.value.pid == os.getpid() and str(os.getpid()) in str(caught.value) and "already running" in str(caught.value)
    finally:
        os.close(first)


def test_it_can_start_again_once_the_first_has_stopped(tmp_path):
    os.close(instance.acquire(tmp_path))
    os.close(instance.acquire(tmp_path))


def test_a_first_that_was_killed_leaves_nothing_behind(tmp_path):
    holder = subprocess.Popen([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(__import__('pathlib').Path(instance.__file__).parents[1])!r});"
                               f"from minty_core import instance; import pathlib, time; instance.acquire(pathlib.Path({str(tmp_path)!r})); print('held', flush=True); time.sleep(60)"],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    with pytest.raises(instance.AlreadyRunning) as caught:
        instance.acquire(tmp_path)
    assert caught.value.pid == holder.pid
    holder.send_signal(signal.SIGKILL)
    holder.wait()
    os.close(instance.acquire(tmp_path))  # the lock went with the process: no stale file blocks the next start


def test_the_lock_file_is_private_and_not_passed_on_to_programs_minty_starts(tmp_path):
    fd = instance.acquire(tmp_path)
    try:
        assert stat.S_IMODE((tmp_path / "minty.lock").stat().st_mode) == 0o600
        assert not os.get_inheritable(fd)  # ffmpeg, a coding agent or bubblewrap must not keep it alive after Minty stops
    finally:
        os.close(fd)


def test_the_run_path_stops_before_anything_is_loaded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    started = []

    async def never(*a, **k):
        started.append(1)

    monkeypatch.setattr(app, "_run", never)
    held = instance.acquire(tmp_path)
    try:
        with pytest.raises(SystemExit) as stop:
            app.main([])
    finally:
        os.close(held)
    assert started == [] and "already running" in str(stop.value) and "7778" in str(stop.value)


def test_the_run_path_starts_when_nothing_else_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    started = []

    async def fake(*a, **k):
        started.append(1)

    monkeypatch.setattr(app, "_run", fake)
    app.main([])
    assert started == [1]


def test_other_commands_are_not_blocked_by_a_running_minty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    held = instance.acquire(tmp_path)
    try:
        app.main(["service-unit"])  # prints the unit; needs no lock
    finally:
        os.close(held)
    assert "ExecStart" in capsys.readouterr().out


def test_an_in_app_restart_replaces_the_process_without_the_lock_getting_in_its_way(tmp_path):
    src = str(__import__("pathlib").Path(instance.__file__).parents[1])
    again = f"import sys; sys.path.insert(0, {src!r}); from minty_core import instance; import pathlib; instance.acquire(pathlib.Path({str(tmp_path)!r})); print('acquired again')"
    first = (f"import os, sys; sys.path.insert(0, {src!r}); from minty_core import instance; import pathlib; "
             f"instance.acquire(pathlib.Path({str(tmp_path)!r})); os.execv(sys.executable, [sys.executable, '-c', {again!r}])")
    done = subprocess.run([sys.executable, "-c", first], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0 and "acquired again" in done.stdout, done.stderr
