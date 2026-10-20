import os
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "install_service.sh"


def test_unit_execstart_is_quoted_and_percent_escaped(tmp_path):
    """One unit now (the panel runs inside the core): its text comes from `minty-core service-unit`, quoted and with
    % doubled for systemd, and it restarts on failure."""
    root = tmp_path / "my 50% checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "install_service.sh").write_text(SCRIPT.read_text())
    (root / ".venv" / "bin").mkdir(parents=True)
    wrapper = root / ".venv" / "bin" / "python"  # a wrapper: a bare symlink would lose the real venv's packages
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    os.symlink(REPO / "src", root / "src")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "systemctl"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    loginctl = bindir / "loginctl"
    loginctl.write_text("#!/bin/sh\necho no\n")
    loginctl.chmod(loginctl.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "USER": "test", "PATH": f"{bindir}:/usr/bin:/bin"}
    out = subprocess.run(["bash", str(root / "scripts" / "install_service.sh")], env=env, check=True,
                         capture_output=True, text=True)
    text = (home / ".config" / "systemd" / "user" / "minty-core.service").read_text()
    assert f'ExecStart="{tmp_path}/my 50%% checkout/.venv/bin/python" -m minty_core' in text.splitlines()
    assert "Restart=on-failure" in text
    assert "loginctl enable-linger test" in out.stdout  # lingering is explained, not enabled silently
