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


def _installer_sandbox(tmp_path):
    """The installer in a throwaway checkout, with a python whose `-m pip` does nothing, so no network and no real environment."""
    root = tmp_path / "checkout"
    (root / "src" / "minty_core").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "bin" / "minty").write_text("#!/bin/sh\necho minty\n")
    (root / "install.sh").write_text((REPO / "install.sh").read_text())
    (root / ".venv" / "bin").mkdir(parents=True)
    venv_python = root / ".venv" / "bin" / "python"
    venv_python.write_text(f'#!/bin/sh\ncase "$*" in *"-m pip"*) exit 0;; esac\nexec "{sys.executable}" "$@"\n')
    venv_python.chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    python3 = tools / "python3"
    python3.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python3.chmod(0o755)
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bindir.mkdir()
    env = {"HOME": str(home), "MINTY_BIN": str(bindir), "PATH": f"{tools}:/usr/bin:/bin", "MINTY_HOME": str(tmp_path / "mh")}
    return root, bindir, env


def _install(root, env, *args):
    return subprocess.run(["bash", str(root / "install.sh"), "--no-onboard", *args], env=env, capture_output=True, text=True)


def test_the_installer_links_the_minty_command_when_there_is_none(tmp_path):
    root, bindir, env = _installer_sandbox(tmp_path)
    done = _install(root, env)
    assert done.returncode == 0, done.stderr
    assert (bindir / "minty").resolve() == (root / "bin" / "minty").resolve()


def test_the_installer_never_replaces_a_minty_command_that_points_somewhere_else(tmp_path):
    root, bindir, env = _installer_sandbox(tmp_path)
    mine = tmp_path / "my-launcher"
    mine.write_text("#!/bin/sh\necho mine\n")
    mine.chmod(0o755)
    (bindir / "minty").symlink_to(mine)
    done = _install(root, env)
    assert done.returncode == 0, done.stderr
    assert (bindir / "minty").resolve() == mine.resolve()  # untouched
    assert "already exists" in done.stdout and "--relink" in done.stdout
    again = _install(root, env, "--relink")  # unless you ask for it
    assert (bindir / "minty").resolve() == (root / "bin" / "minty").resolve() and again.returncode == 0


def test_the_installer_leaves_a_command_that_already_points_here_alone(tmp_path):
    root, bindir, env = _installer_sandbox(tmp_path)
    assert _install(root, env).returncode == 0
    second = _install(root, env)
    assert second.returncode == 0 and "already points here" in second.stdout
