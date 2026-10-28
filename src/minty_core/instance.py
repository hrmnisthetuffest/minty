"""One Minty per settings folder.

The lock is an advisory `flock` on `minty.lock` in the settings folder, held for as long as the process lives. The kernel drops it when the
process ends, however it ends (a crash or a kill included), so there is never a stale lock to clear by hand. The file holds the process
number so the message can name it. Taken before anything else starts, so a second `minty` does not load models, poll Telegram on the same bot
or open the microphone only to fail at the port.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


class AlreadyRunning(Exception):
    def __init__(self, pid: int | None):
        self.pid = pid
        who = f"process {pid}" if pid else "another process"
        super().__init__(f"Minty is already running ({who}, with these same settings). Open the panel at http://127.0.0.1:7778, "
                         "or stop that one first (Ctrl-C in its terminal) and start again.")


def acquire(config_dir: Path) -> int:
    """Take the lock and keep the descriptor open (do not close it while Minty runs). Raises AlreadyRunning when it is held."""
    path = Path(config_dir) / "minty.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)  # not inherited by programs Minty starts (Python opens close-on-exec)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            pid = int(os.pread(fd, 32, 0).decode().strip() or 0) or None
        except (OSError, ValueError):
            pid = None
        os.close(fd)
        raise AlreadyRunning(pid) from None
    except BaseException:
        os.close(fd)
        raise
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
    return fd
