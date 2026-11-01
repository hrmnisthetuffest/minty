import os
import tempfile
import time
from pathlib import Path

from minty_core import osutil

_REPLACE_TRIES = 8  # Windows only: an antivirus scan or the indexer can hold the file for a moment, and replacing it then fails with "access denied"
_REPLACE_WAIT_S = 0.05


def replace_file(src, dst) -> None:
    """`os.replace`, patient on Windows (where it can fail while something else has either file open) and plain on other systems."""
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not osutil.IS_WINDOWS or attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(_REPLACE_WAIT_S * (attempt + 1))


def atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Write a whole file or nothing. Always UTF-8 and always \\n line ends, so a file is the same bytes on Windows and Linux (a backup or a copy made on one restores on the other)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        replace_file(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
