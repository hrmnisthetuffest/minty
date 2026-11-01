"""Everything that differs between Windows and Linux lives here, so the rest of Minty does not have to know.

Linux is the reference (it is what Minty was written on); Windows is supported through the branches below. macOS is not claimed: nothing here is meant to make it worse, nothing is checked on it.
The rule, enforced by a test (`tests/test_portability.py`): the POSIX-only names (`os.killpg`, `signal.SIGKILL`, `os.fchmod`, `preexec_fn`, `fcntl`, `termios`, `resource` ...) appear in this
module or in a place that guards them, and nowhere else.

What is here: starting a child in its own process group and stopping the whole tree, resource limits for a child, this process's memory and open handles, opening a file or web address with the
desktop's default program, and the console (UTF-8, ANSI colours, single key presses). Each function says what it does on the other system.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from typing import Callable

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"

# Windows process-creation flags (the constants exist in `subprocess` only on Windows, so they are written out)
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


# ---- child processes ------------------------------------------------------------------------------------------------------------------------
def group_kwargs(*, show_window: bool = False) -> dict:
    """Arguments for `subprocess.Popen` / `asyncio.create_subprocess_exec` so the child heads its own process group (POSIX: its own session). Stopping it with
    `terminate_tree` / `kill_tree` then stops everything it started. On Windows a child also gets no console window of its own unless `show_window`."""
    if IS_WINDOWS:
        flags = CREATE_NEW_PROCESS_GROUP | (0 if show_window else CREATE_NO_WINDOW)
        return {"creationflags": flags}
    return {"start_new_session": True}


def terminate_tree(proc) -> None:
    """Ask a child (and what it started) to stop politely. POSIX: SIGTERM to its group. Windows: a break signal to its group (a console program may ignore it, which is what
    `kill_tree` is for). Never raises."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError, ValueError):
        if IS_WINDOWS:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            os.killpg(proc.pid, signal.SIGTERM)


def kill_tree(proc) -> None:
    """Stop a child and everything it started, now. POSIX: SIGKILL to its group. Windows: `taskkill /T /F`. Never raises."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError, ValueError):
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                           creationflags=CREATE_NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGKILL)


def limits_preexec(cpu_s: float | None = None, mem_mb: int | None = None) -> Callable[[], None] | None:
    """A function to run in a child before it starts that caps its CPU time and address space, or None where that cannot be done (Windows has no `preexec_fn` at all:
    passing one raises, so None must be passed through as it is)."""
    if IS_WINDOWS:
        return None
    import resource

    def limit() -> None:
        if cpu_s:
            resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_s), int(cpu_s)))
        if mem_mb:
            resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 2**20, mem_mb * 2**20))

    return limit


def replace_process(argv: list[str]) -> None:
    """Become `argv`. POSIX: replace this process (`exec`). Windows has no exec that replaces anything (the shell would think we had finished while the new one still writes to the
    console), so the new process runs as a child on the same console, this one waits for it and exits with its code. Does not return."""
    if not IS_WINDOWS:
        os.execv(argv[0], argv)
        return  # never reached for real (exec does not come back); here so that nothing can fall through into the Windows branch
    code = subprocess.call(argv)
    os._exit(code)


# ---- this process ---------------------------------------------------------------------------------------------------------------------------
def rss_mb() -> float:
    """Memory this process holds in RAM, in MB (0.0 when it cannot be read)."""
    if IS_WINDOWS:
        return _windows_process_info()[0]
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except (ImportError, OSError):
        return 0.0


def open_handles() -> int:
    """How many files, sockets and pipes this process has open (a leak shows up as a number that only grows). 0 when it cannot be read."""
    if IS_WINDOWS:
        return int(_windows_process_info()[1])
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return 0


def _windows_process_info() -> tuple[float, int]:
    """(working set in MB, handle count) from the Windows API; (0.0, 0) when it cannot be asked."""
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi  # type: ignore[attr-defined]
        handle = kernel.GetCurrentProcess()
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        mb = counters.WorkingSetSize / 2**20 if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb) else 0.0
        count = wintypes.DWORD()
        handles = count.value if kernel.GetProcessHandleCount(handle, ctypes.byref(count)) else 0
        return mb, handles
    except Exception:  # noqa: BLE001 — a monitor must never be what fails
        return 0.0, 0


# ---- opening things -------------------------------------------------------------------------------------------------------------------------
def open_with_default_app(target: str) -> bool:
    """Open a web address or a file with the program the desktop has for it. True when it was handed over. Windows: `os.startfile`. macOS: `open`. Linux: `xdg-open`."""
    try:
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 — the caller has judged the target
            return True
        argv = ["open" if IS_MAC else "xdg-open", target]
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **group_kwargs())
        return True
    except OSError:
        return False


# ---- the console ----------------------------------------------------------------------------------------------------------------------------
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
UTF8_CODE_PAGE = 65001


def prepare_console() -> None:
    """Make the console behave like the Linux one: UTF-8 text in and out (Windows' default code page garbles anything that is not plain English), ANSI colours and cursor
    movement on (Windows 10 and later have them, off by default in a classic console), and a child's text decoded as UTF-8 too. A no-op on other systems. Never raises."""
    if not IS_WINDOWS:
        return
    os.environ.setdefault("PYTHONUTF8", "1")  # programs Minty starts inherit it
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, OSError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    with contextlib.suppress(Exception):
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel.SetConsoleOutputCP(UTF8_CODE_PAGE)
        kernel.SetConsoleCP(UTF8_CODE_PAGE)
        for std in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel.GetStdHandle(std)
            mode = ctypes.c_uint32()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)


# What the Windows console sends for a special key: a prefix character (NUL or 0xE0) then a code. Mapped to the ANSI text a Linux terminal sends,
# so everything above this (key parsing, menus) is the same on both.
_WINDOWS_SPECIAL = {"H": "\x1b[A", "P": "\x1b[B", "M": "\x1b[C", "K": "\x1b[D", "G": "\x1b[H", "O": "\x1b[F", "I": "\x1b[5~", "Q": "\x1b[6~", "S": "\x1b[3~", "R": "\x1b[2~"}


def windows_key_text(first: str, second: str = "") -> str:
    """Text for one Windows console key: `first` is what `msvcrt.getwch()` returned and `second` the follow-up it returned when `first` was a prefix (NUL or 0xE0)."""
    if first in ("\x00", "\xe0"):
        return _WINDOWS_SPECIAL.get(second, "")  # an unmapped function key is dropped
    return first


def windows_read_keys(block: bool = True) -> str:
    """Everything typed since the last call as the text a Linux terminal would have sent. Blocks for the first key when `block`, then takes whatever else is waiting (a
    paste arrives as many keys). Windows only."""
    import msvcrt

    out: list[str] = []
    if block or msvcrt.kbhit():
        out.append(_one_windows_key(msvcrt))
    while msvcrt.kbhit():
        out.append(_one_windows_key(msvcrt))
    return "".join(out)


def _one_windows_key(msvcrt_module) -> str:
    first = msvcrt_module.getwch()
    second = msvcrt_module.getwch() if first in ("\x00", "\xe0") else ""
    return windows_key_text(first, second)
