"""The systemd user unit that keeps Minty running: restart on failure, a crash-loop ceiling, gentle on the laptop.

`minty-core service-unit` prints it; scripts/install_service.sh installs it. Lingering (`loginctl enable-linger`)
lets it start at boot without a login; the installer says so instead of doing it silently.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_MEMORY_HIGH_MB = 1500


def unit_text(root: Path, *, description: str = "Minty (voice assistant)", python: str | None = None,
              memory_high_mb: int = DEFAULT_MEMORY_HIGH_MB) -> str:
    root = Path(root)
    exe = python or str(root / ".venv" / "bin" / "python")
    esc = lambda s: str(s).replace("%", "%%")  # noqa: E731 — systemd specifiers
    return f"""[Unit]
Description={description}
After=network-online.target sound.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory={esc(root)}
Environment=PYTHONPATH={esc(root / 'src')}
Environment=MALLOC_ARENA_MAX=2
Environment=PATH=%h/.local/bin:%h/.npm-global/bin:/usr/local/bin:/usr/bin:/bin
ExecStart="{esc(exe)}" -m minty_core
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
KillMode=mixed
Nice=5
MemoryHigh={memory_high_mb}M

[Install]
WantedBy=default.target
"""
