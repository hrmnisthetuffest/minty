"""The systemd unit (W1B-12)."""
import subprocess
import sys
from pathlib import Path

from minty_core.service import unit_text

ROOT = Path(__file__).resolve().parents[1]


def test_the_unit_restarts_on_failure_with_a_crash_loop_ceiling():
    text = unit_text(Path("/home/me/Minty"))
    for line in ("Restart=on-failure", "RestartSec=3", "StartLimitBurst=5", "StartLimitIntervalSec=300",
                 "WantedBy=default.target", "MemoryHigh=1500M", "Wants=network-online.target",
                 'ExecStart="/home/me/Minty/.venv/bin/python" -m minty_core',
                 "WorkingDirectory=/home/me/Minty", "Environment=PYTHONPATH=/home/me/Minty/src"):
        assert line in text, line


def test_percent_signs_in_paths_are_escaped_for_systemd():
    assert "WorkingDirectory=/home/a%%b" in unit_text(Path("/home/a%b"))


def test_the_command_prints_it():
    out = subprocess.run([sys.executable, "-c", "from minty_core.app import main; main(['service-unit'])"],
                         capture_output=True, text=True, cwd=ROOT,
                         env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0 and "Restart=on-failure" in out.stdout and str(ROOT) in out.stdout


def test_the_service_finds_tools_installed_in_the_home_folder():
    """A user service does not read ~/.profile, so without this `claude`, `hermes`, `openclaw` and `yt-dlp`
    (installed under ~/.local/bin or an npm prefix in the home folder) would not be found when Minty runs as a service."""
    path = next(l for l in unit_text(Path("/home/me/Minty")).splitlines() if l.startswith("Environment=PATH="))
    dirs = path.split("=", 2)[2].split(":")
    assert dirs[0] == "%h/.local/bin" and "%h/.npm-global/bin" in dirs[:3]
    assert {"/usr/local/bin", "/usr/bin", "/bin"} <= set(dirs)


def test_the_service_limits_memory_arenas_for_small_machines():
    text = unit_text(Path("/opt/minty"))
    assert "Environment=MALLOC_ARENA_MAX=2" in text  # glibc keeps one memory arena per core by default: wasteful on 4 GB
