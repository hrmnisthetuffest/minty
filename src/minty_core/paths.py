import os
from pathlib import Path


def config_dir() -> Path:
    override = os.environ.get("MINTY_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "minty"
