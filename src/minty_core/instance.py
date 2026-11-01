"""One Minty per settings folder.

POSIX: an advisory `flock` on `minty.lock` in the settings folder, held for as long as the process lives. The kernel drops it when the process ends, however it ends (a crash or a kill
included), so there is never a stale lock to clear by hand. Windows: the same idea with `msvcrt.locking` on one byte far past the end of the file (Windows locks are mandatory, so
the range that holds the lock cannot be read; the process number is kept at the start of the file, outside it, so the message can still name it). Windows also drops it when the
process ends. Taken before anything else starts, so a second `minty` does not load models, poll Telegram on the same bot or open the microphone only to fail at the port.
"""
from __future__ import annotations

import os
from pathlib import Path

from minty_core import osutil

LOCK_OFFSET = 0x7FFFFFF0  # Windows: the byte that is locked, well beyond anything written to the file


class AlreadyRunning(Exception):
    def __init__(self, pid: int | None):
        self.pid = pid
        who = f"process {pid}" if pid else "another process"
        super().__init__(f"Minty is already running ({who}, with these same settings). Open the panel at http://127.0.0.1:7778, "
                         "or stop that one first (Ctrl-C in its terminal) and start again.")


def _read_pid(fd: int) -> int | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return int(os.read(fd, 32).decode().strip() or 0) or None
    except (OSError, ValueError):
        return None


def _write_pid(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())


def _lock(fd: int) -> bool:
    """True when this process now holds the lock, False when another one does."""
    if osutil.IS_WINDOWS:
        import msvcrt

        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def acquire(config_dir: Path) -> int:
    """Take the lock and keep the descriptor open (do not close it while Minty runs). Raises AlreadyRunning when it is held."""
    path = Path(config_dir) / "minty.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)  # not inherited by programs Minty starts (Python opens close-on-exec)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)  # Windows has no such call: the profile folder's own permissions keep the file private
        if not _lock(fd):
            pid = _read_pid(fd)
            os.close(fd)
            raise AlreadyRunning(pid)
    except AlreadyRunning:
        raise
    except BaseException:
        os.close(fd)
        raise
    _write_pid(fd)
    return fd
