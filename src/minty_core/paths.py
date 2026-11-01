"""Where Minty keeps its things.

Linux (and macOS): settings, keys and every database in `$XDG_CONFIG_HOME/minty` (`~/.config/minty`); downloaded models and voices in `$XDG_DATA_HOME/minty` (`~/.local/share/minty`).
Windows: both under `%LOCALAPPDATA%\\Minty` (settings and databases directly in it, models and voices in its `data` folder). Local rather than roaming on purpose: it holds secrets and big
downloads that must not follow an account around a company network. The folder belongs to the signed-in user; Windows gives it that user's own permissions, which is what keeps it private
(there is no "mode 0600" to set).
`MINTY_CONFIG_DIR` overrides the settings folder on every system.
"""
import os
import sys
from pathlib import Path


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))


def config_dir() -> Path:
    override = os.environ.get("MINTY_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return _local_appdata() / "Minty"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "minty"


def data_dir() -> Path:
    """Large downloads (voice models). Next to the settings on Windows, in the XDG data folder elsewhere."""
    if sys.platform == "win32":
        return _local_appdata() / "Minty" / "data"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "minty"
