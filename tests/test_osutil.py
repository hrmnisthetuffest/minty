"""The OS layer: what runs on Linux is run for real, and what only runs on Windows is driven through fakes of exactly the calls it makes (osutil.py, instance.py, fsutil.py, paths.py)."""
import os
import pathlib
import signal
import subprocess
import sys
import time
import types

import pytest

from minty_core import fsutil, instance, osutil, paths

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses real POSIX process groups and signals (on Windows `os.kill(pid, 0)` would terminate the process)")


# ---- processes ---------------------------------------------------------------------------------------------------------------------------
def test_a_child_gets_its_own_group_on_linux_and_its_own_group_without_a_console_window_on_windows(monkeypatch):
    monkeypatch.setattr(osutil, "IS_WINDOWS", False)
    assert osutil.group_kwargs() == {"start_new_session": True}
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    assert osutil.group_kwargs() == {"creationflags": osutil.CREATE_NEW_PROCESS_GROUP | osutil.CREATE_NO_WINDOW}
    assert osutil.group_kwargs(show_window=True) == {"creationflags": osutil.CREATE_NEW_PROCESS_GROUP}


@posix_only
def test_stopping_a_child_stops_what_it_started_too(tmp_path):
    marker = tmp_path / "grandchild.pid"
    code = f"import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); open({str(marker)!r}, 'w').write(str(p.pid)); time.sleep(60)"
    proc = subprocess.Popen([sys.executable, "-c", code], **osutil.group_kwargs())
    for _ in range(100):
        if marker.exists() and marker.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    grandchild = int(marker.read_text(encoding="utf-8"))
    osutil.kill_tree(proc)
    proc.wait(timeout=5)
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild, 0)  # the grandchild went with it


@posix_only
def test_a_polite_stop_reaches_the_child_and_never_raises_for_one_that_is_gone():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **osutil.group_kwargs())
    osutil.terminate_tree(proc)
    assert proc.wait(timeout=5) == -signal.SIGTERM
    osutil.terminate_tree(proc)
    osutil.kill_tree(proc)  # already gone: nothing to say


def test_on_windows_the_tree_is_stopped_with_taskkill_and_the_polite_stop_is_a_break_signal(monkeypatch):
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    calls = []
    monkeypatch.setattr(osutil.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    sent = []
    proc = types.SimpleNamespace(pid=4321, send_signal=sent.append)
    monkeypatch.setattr(osutil.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    osutil.kill_tree(proc)
    osutil.terminate_tree(proc)
    assert calls[0][0] == ["taskkill", "/PID", "4321", "/T", "/F"] and calls[0][1]["creationflags"] == osutil.CREATE_NO_WINDOW and sent == [1]
    monkeypatch.setattr(osutil.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no taskkill")))
    osutil.kill_tree(proc)  # a failing helper does not raise


@posix_only
def test_resource_limits_are_a_function_on_linux_and_nothing_on_windows(monkeypatch):
    assert callable(osutil.limits_preexec(cpu_s=5, mem_mb=512))
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    assert osutil.limits_preexec(cpu_s=5, mem_mb=512) is None  # preexec_fn raises on Windows; None is accepted everywhere


def test_the_new_process_replaces_this_one_on_linux_and_is_waited_for_on_windows(monkeypatch):
    exec_calls = []
    monkeypatch.setattr(osutil, "IS_WINDOWS", False)
    monkeypatch.setattr(osutil.os, "execv", lambda exe, argv: exec_calls.append((exe, argv)))
    osutil.replace_process(["/usr/bin/python", "-m", "minty_core"])
    assert exec_calls == [("/usr/bin/python", ["/usr/bin/python", "-m", "minty_core"])]
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    ran, left = [], []
    monkeypatch.setattr(osutil.subprocess, "call", lambda argv: (ran.append(argv), 7)[1])
    monkeypatch.setattr(osutil.os, "_exit", left.append)
    osutil.replace_process(["python.exe", "-m", "minty_core"])
    assert ran == [["python.exe", "-m", "minty_core"]] and left == [7] and exec_calls.__len__() == 1


# ---- this process ------------------------------------------------------------------------------------------------------------------------
def test_memory_and_open_handles_are_real_numbers():
    assert osutil.rss_mb() > 10
    keep = [open(__file__, "rb") for _ in range(5)]
    assert osutil.open_handles() >= 5
    for f in keep:
        f.close()


def test_opening_a_file_or_address_uses_the_systems_own_opener(monkeypatch):
    seen = []
    monkeypatch.setattr(osutil, "IS_WINDOWS", False)
    monkeypatch.setattr(osutil, "IS_MAC", False)
    monkeypatch.setattr(osutil.subprocess, "Popen", lambda argv, **kw: seen.append(argv))
    assert osutil.open_with_default_app("https://example.test") and seen == [["xdg-open", "https://example.test"]]
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    started = []
    monkeypatch.setattr(osutil.os, "startfile", started.append, raising=False)
    assert osutil.open_with_default_app("https://example.test") and started == ["https://example.test"]
    monkeypatch.setattr(osutil.os, "startfile", lambda t: (_ for _ in ()).throw(OSError("no handler")), raising=False)
    assert osutil.open_with_default_app("x.unknownext") is False


def test_the_console_is_left_alone_on_linux_and_set_to_utf8_on_windows(monkeypatch):
    before = dict(os.environ)
    monkeypatch.setattr(osutil, "IS_WINDOWS", False)
    osutil.prepare_console()
    assert dict(os.environ) == before
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    osutil.prepare_console()  # no ctypes.windll here: every console call is guarded, and the environment is still set
    assert os.environ["PYTHONUTF8"] == "1" and os.environ["PYTHONIOENCODING"] == "utf-8"


# ---- keys on the Windows console ---------------------------------------------------------------------------------------------------------
def test_windows_special_keys_become_the_text_a_linux_terminal_sends():
    assert osutil.windows_key_text("a") == "a" and osutil.windows_key_text("\r") == "\r"
    assert osutil.windows_key_text("\xe0", "H") == "\x1b[A" and osutil.windows_key_text("\x00", "P") == "\x1b[B"
    assert osutil.windows_key_text("\xe0", "S") == "\x1b[3~" and osutil.windows_key_text("\x00", "\x86") == ""  # an unmapped function key is dropped


def test_a_paste_arrives_as_one_chunk_and_the_first_read_waits(monkeypatch):
    keys = list("héllo") + ["\xe0", "H", "!"]
    fake = types.SimpleNamespace(kbhit=lambda: bool(keys), getwch=lambda: keys.pop(0))
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    assert osutil.windows_read_keys(block=True) == "héllo\x1b[A!"
    assert osutil.windows_read_keys(block=False) == ""


# ---- the single-instance lock on Windows ---------------------------------------------------------------------------------------------------
class FakeMsvcrt:
    LK_NBLCK = 2

    def __init__(self, busy=False):
        self.busy, self.calls = busy, []

    def locking(self, fd, mode, nbytes):
        self.calls.append((mode, nbytes, os.lseek(fd, 0, os.SEEK_CUR)))
        if self.busy:
            raise OSError(13, "Permission denied")


def test_on_windows_the_lock_is_one_byte_far_past_the_end_so_the_process_number_stays_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    fake = FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.delattr(os, "fchmod", raising=False)  # Windows has none: the lock must not need it
    fd = instance.acquire(tmp_path)
    assert fake.calls == [(2, 1, instance.LOCK_OFFSET)]
    assert (tmp_path / "minty.lock").read_text(encoding="utf-8").strip() == str(os.getpid())
    os.close(fd)


def test_on_windows_a_second_minty_is_told_who_holds_it(tmp_path, monkeypatch):
    (tmp_path / "minty.lock").write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt(busy=True))
    with pytest.raises(instance.AlreadyRunning) as caught:
        instance.acquire(tmp_path)
    assert caught.value.pid == 4242 and "already running" in str(caught.value)


# ---- files ----------------------------------------------------------------------------------------------------------------------------------
def test_files_are_written_with_unix_line_ends_and_utf8_whatever_the_system(tmp_path):
    target = tmp_path / "note.md"
    fsutil.atomic_write_text(target, "line one\nline two — café\n")
    assert target.read_bytes() == "line one\nline two — café\n".encode("utf-8")


def test_the_writer_asks_for_unix_line_ends_explicitly_because_linux_cannot_show_the_difference():
    """On Linux a text file never has its \\n rewritten, so no run here can tell whether `newline="\\n"` is there; on Windows, without it, every stored file would come out with \\r\\n."""
    import ast

    tree = ast.parse((pathlib.Path(fsutil.__file__)).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "fdopen"]
    assert calls and all({k.arg: getattr(k.value, "value", None) for k in c.keywords}.get("newline") == "\n" for c in calls)


def test_on_windows_a_file_someone_else_has_open_is_retried_briefly_then_the_error_stands(tmp_path, monkeypatch):
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    real, attempts = os.replace, []

    def flaky(src, dst):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError(13, "in use by another process")
        real(src, dst)

    monkeypatch.setattr(fsutil.os, "replace", flaky)
    fsutil.atomic_write_text(tmp_path / "a.txt", "x")
    assert len(attempts) == 3 and (tmp_path / "a.txt").read_text(encoding="utf-8") == "x"
    monkeypatch.setattr(fsutil.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(13, "always")))
    with pytest.raises(PermissionError):
        fsutil.atomic_write_text(tmp_path / "b.txt", "y")


def test_on_linux_a_permission_error_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(osutil, "IS_WINDOWS", False)
    attempts = []
    monkeypatch.setattr(fsutil.os, "replace", lambda s, d: (attempts.append(1), (_ for _ in ()).throw(PermissionError(13, "denied")))[1])
    with pytest.raises(PermissionError):
        fsutil.atomic_write_text(tmp_path / "a.txt", "x")
    assert len(attempts) == 1


# ---- where things live --------------------------------------------------------------------------------------------------------------------
def test_linux_keeps_its_xdg_folders_and_the_override_wins_everywhere(tmp_path, monkeypatch):
    monkeypatch.delenv("MINTY_CONFIG_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert paths.config_dir() == tmp_path / "cfg" / "minty" and paths.data_dir() == tmp_path / "data" / "minty"
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "mine"))
    assert paths.config_dir() == tmp_path / "mine"


def test_windows_keeps_everything_under_local_app_data(tmp_path, monkeypatch):
    monkeypatch.delenv("MINTY_CONFIG_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert paths.config_dir() == tmp_path / "Local" / "Minty" and paths.data_dir() == tmp_path / "Local" / "Minty" / "data"
    monkeypatch.delenv("LOCALAPPDATA")
    assert paths.config_dir().parts[-3:] == ("AppData", "Local", "Minty")  # no variable set: the standard place under the profile


# ---- the terminal on Windows ----------------------------------------------------------------------------------------------------------
async def test_the_tui_reads_keys_on_windows_through_a_thread_and_ctrl_c_becomes_an_interrupt(monkeypatch):
    import asyncio

    from minty_core import tui

    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    chunks = iter(["h", "i\x03", "\r"])

    def read(block=True):
        try:
            return next(chunks)
        except StopIteration:
            raise OSError("console closed")

    monkeypatch.setattr(osutil, "windows_read_keys", read)
    interrupted = []
    monkeypatch.setattr("_thread.interrupt_main", lambda *a: interrupted.append(1))
    reader = tui._KeyReader(types.SimpleNamespace(), lambda line: None, None)
    got = []
    reader.feed = got.append
    reader.start()
    for _ in range(100):
        if len(got) >= 3 and interrupted:
            break
        await asyncio.sleep(0.02)
    reader.stop()
    assert got == ["h", "i", "\r"] and interrupted == [1]  # the Ctrl-C itself is not typed into the box


def test_the_onboarding_menus_read_windows_keys_as_the_names_the_menus_know(monkeypatch):
    from minty_core.onboard import ui

    def keys(*typed):
        queue = list(typed)
        monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(getwch=lambda: queue.pop(0)))

    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    for typed, want in ((("\r",), "enter"), ((" ",), "space"), (("\x1b",), "esc"), (("\xe0", "H"), "up"), (("\x00", "P"), "down"), (("y",), "y"), (("\xe0", "K"), "")):
        keys(*typed)
        assert ui.read_key() == want, typed
    keys("\x03")
    with pytest.raises(KeyboardInterrupt):
        ui.read_key()
    with ui.cbreak_mode():  # nothing to switch on Windows
        pass


def test_the_onboarding_ui_is_interactive_on_a_windows_terminal(monkeypatch):
    from rich.console import Console

    from minty_core.onboard import ui

    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "termios", None)  # Windows has none
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.delenv("TERM", raising=False)
    console = Console(force_terminal=True)
    assert ui.RichUI(console).interactive is True


# ---- names Windows will not accept ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name, want", [("con", "_con"), ("NUL", "_NUL"), ("aux.txt", "_aux.txt"), ("Com1", "_Com1"), ("lpt9", "_lpt9"), ("notes", "notes"), ("console", "console"),
                                        ("report.", "report"), ("trailing ", "trailing"), ("", "_")])
def test_a_file_or_folder_name_is_never_a_windows_device_name(name, want):
    assert fsutil.safe_stem(name) == want


def test_the_research_library_and_the_vault_use_it_so_a_topic_called_con_still_gets_a_folder(tmp_path):
    from minty_core.research.library import slugify
    from minty_core.vault.store import Vault

    assert slugify("con") == "_con" and slugify("Coral reefs") == "coral-reefs"
    vault = Vault(tmp_path / "v", git=False)
    note = vault.create("Anything", "body", filename="nul.md")
    assert not note.path.split("/")[-1].lower().startswith("nul")
