"""The Windows installer, launchers and checks (install.ps1, install.cmd, bin/minty.cmd, scripts/windows_check.ps1, scripts/install_check.py, scripts/doctor.py, autostart.py).
What Linux can prove: the files are what Windows PowerShell 5.1 and cmd.exe can read, they do the same things install.sh does, and the checks they run pass here. Running them on Windows is the
CI job's work (.github/workflows/ci.yml, job `windows`)."""
import os
import re
import subprocess
import sys

import pytest

from minty_core import autostart, osutil

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]
PS1 = REPO / "install.ps1"
CHECK_PS1 = REPO / "scripts" / "windows_check.ps1"


def read_bytes(path):
    return path.read_bytes()


# ---- the files Windows reads ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", [PS1, CHECK_PS1])
def test_powershell_scripts_are_plain_ascii_because_windows_powershell_5_1_reads_them_in_the_old_code_page(path):
    data = read_bytes(path)
    assert not data.startswith(b"\xef\xbb\xbf") and all(b < 128 for b in data), [i for i, b in enumerate(data) if b >= 128][:5]


@pytest.mark.parametrize("path", [PS1, CHECK_PS1])
def test_powershell_scripts_avoid_what_windows_powershell_5_1_cannot_parse(path):
    text = path.read_text(encoding="ascii")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r"<#.*?#>", "", code, flags=re.S)
    assert "&&" not in code and "||" not in code  # pipeline chain operators are PowerShell 7
    assert " ?? " not in code and "?." not in code and " ? " not in code  # null-coalescing and ternary are PowerShell 7
    assert "ForEach-Object -Parallel" not in code and "-AsHashtable" not in code
    assert "setx " not in code.lower()  # setx cuts a PATH at 1024 characters


def test_the_batch_launchers_have_windows_line_ends_and_git_is_told_not_to_touch_them():
    for name in ("install.cmd", "bin/minty.cmd"):
        data = read_bytes(REPO / name)
        assert data.count(b"\r\n") == data.count(b"\n") > 5 and b"\r\r" not in data and all(b < 128 for b in data), name
    attributes = (REPO / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert attributes.index("*.cmd -text") > attributes.index("* text=auto eol=lf")  # later lines win: the launchers must not be overridden by the catch-all
    result = subprocess.run(["git", "check-attr", "text", "--", "install.cmd", "bin/minty.cmd", "install.ps1"], cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    if result.returncode == 0:
        assert "install.cmd: text: unset" in result.stdout and "bin/minty.cmd: text: unset" in result.stdout and "install.ps1: text: auto" in result.stdout


def test_the_double_click_installer_relaxes_the_script_policy_for_one_run_only():
    text = (REPO / "install.cmd").read_text(encoding="ascii")
    assert "-ExecutionPolicy Bypass" in text and "install.ps1" in text and "Set-ExecutionPolicy" not in text and "%*" in text


def test_the_windows_launcher_does_what_bin_minty_does_and_sets_utf8():
    text = (REPO / "bin" / "minty.cmd").read_text(encoding="ascii")
    assert r".venv\Scripts\python.exe" in text and "MINTY_PYTHON" in text and "PYTHONUTF8=1" in text and "-m minty_core %*" in text
    assert "%PYTHONPATH%\"" not in text.replace('%PYTHONPATH%")', "") or "if defined PYTHONPATH" in text  # an empty PYTHONPATH entry would put the current folder on the path
    assert "if defined PYTHONPATH" in text


def test_the_installer_offers_what_install_sh_offers():
    text = PS1.read_text(encoding="ascii")
    for switch in ("NoOnboard", "Relink", "Uninstall", "Yes", "Python", "Repo", "Branch"):
        assert f"${switch}" in text, switch
    for variable in ("MINTY_REPO", "MINTY_HOME", "MINTY_BIN", "MINTY_BRANCH"):
        assert variable in text
    assert "minty onboard" in text and "minty.cmd" in text and "venv" in text and "-e $root" in text  # the same steps as install.sh
    assert 'SetEnvironmentVariable("Path"' in text and '"User"' in text  # the user's PATH, never the machine's
    assert "Tls12" in text and "$ProgressPreference" in text  # GitHub rejects old TLS; 5.1 downloads crawl with the progress bar
    assert "WindowsApps" in text or "9009" in text  # the Microsoft Store python stub is recognised and ignored
    assert "winget" in text and "Read-Host" in text  # Python is offered and asked about, never installed silently
    assert "https://github.com/hrmnisthetuffest/minty.git" in text and "Set MINTY_REPO" not in text  # the published repository is the default: no "unpublished" refusal


def test_the_installer_never_asks_for_administrator_rights_or_changes_the_machine():
    text = PS1.read_text(encoding="ascii").lower()
    for forbidden in ("runas", "-verb runas", "set-executionpolicy", "hklm:\\software\\microsoft\\windows\\currentversion\\run", '"machine"', "enable-windowsoptionalfeature"):
        assert forbidden not in text.replace("hklm:\\software\\microsoft\\visualstudio", ""), forbidden


def test_the_installer_is_wrapped_so_a_failure_does_not_close_the_window_of_someone_running_it_from_the_web():
    text = PS1.read_text(encoding="ascii")
    assert "try {\n  Install-Minty\n} catch {" in text and "if ($PSCommandPath) { exit 1 }" in text


# ---- the Python parts ----------------------------------------------------------------------------------------------------------------------
class Scheduler:
    def __init__(self, existing=False, fail=False):
        self.calls, self.existing, self.fail = [], existing, fail

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[1] == "/Query":
            return (0 if self.existing else 1), ""
        return (1, "Access is denied.") if self.fail else (0, "SUCCESS")


@pytest.fixture
def windows(monkeypatch, tmp_path):
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("", encoding="utf-8")
    (scripts / "pythonw.exe").write_text("", encoding="utf-8")
    return tmp_path


def test_start_at_login_is_one_per_user_task_with_no_window_and_a_delay(windows):
    run = Scheduler()
    got = autostart.enable(windows, run)
    assert got.ok and "log in" in got.message
    argv = run.calls[0]
    assert argv[:2] == ["schtasks", "/Create"] and argv[argv.index("/TN") + 1] == "Minty" and argv[argv.index("/SC") + 1] == "ONLOGON"
    assert argv[argv.index("/DELAY") + 1] == "0000:30" and argv[argv.index("/RL") + 1] == "LIMITED" and "/F" in argv  # no administrator rights
    command = argv[argv.index("/TR") + 1]
    assert command == f'"{windows / ".venv" / "Scripts" / "pythonw.exe"}" -X utf8 -m minty_core'  # pythonw: no console window; UTF-8 on


def test_start_at_login_says_what_is_wrong_instead_of_failing_silently(windows, tmp_path):
    assert not autostart.enable(windows, Scheduler(fail=True)).ok and "Access is denied" in autostart.enable(windows, Scheduler(fail=True)).message
    assert "run install.ps1 first" in autostart.enable(tmp_path / "nowhere", Scheduler()).message
    long_root = windows / ("x" * 240)
    (long_root / ".venv" / "Scripts").mkdir(parents=True)
    (long_root / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    assert "too long" in autostart.enable(long_root, Scheduler()).message


def test_turning_it_off_removes_only_its_own_task_and_is_calm_when_there_is_none(windows):
    present = Scheduler(existing=True)
    assert autostart.disable(present).ok and ["schtasks", "/Delete", "/TN", "Minty", "/F"] in present.calls
    absent = Scheduler(existing=False)
    assert autostart.disable(absent).ok and not any(c[1] == "/Delete" for c in absent.calls)
    assert autostart.status(Scheduler(existing=True)) is True and autostart.status(Scheduler(existing=False)) is False


def test_on_linux_autostart_points_at_systemd_and_touches_nothing(monkeypatch):
    monkeypatch.setattr(osutil, "IS_WINDOWS", False)
    run = Scheduler()
    assert not autostart.enable(REPO, run).ok and "systemd" in autostart.enable(REPO, run).message
    assert autostart.status(run) is False and run.calls == []


def test_the_command_line_has_an_autostart_command(capsys, monkeypatch):
    from minty_core import app

    monkeypatch.setattr(osutil, "IS_WINDOWS", False)
    app.main(["autostart", "status"])
    assert "does not start at login" in capsys.readouterr().out
    with pytest.raises(SystemExit) as caught:
        app.main(["autostart", "on"])
    assert caught.value.code == 1


def test_the_doctor_checks_windows_things_on_windows_and_not_linux_ones():
    import importlib.util

    spec = importlib.util.spec_from_file_location("doctor", REPO / "scripts" / "doctor.py")
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    names = {c[0] for c in doctor.WINDOWS_BINARIES}
    assert {"ffmpeg", "git", "node"} <= names and not names & {"playerctl", "pactl", "parec", "xprop", "wpctl"}
    assert all(c[3] for c in doctor.WINDOWS_BINARIES)  # nothing on Windows is "required": Minty runs without each
    assert "VCRedist" in doctor.VCREDIST


# ---- the install check, really run -------------------------------------------------------------------------------------------------------
def run_check(*args, timeout=240):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"), PYTHONUTF8="1")
    return subprocess.run([sys.executable, str(REPO / "scripts" / "install_check.py"), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout, cwd=str(REPO))


def test_the_install_check_passes_here_without_starting_minty():
    done = run_check("--quick")
    assert done.returncode == 0, done.stdout + done.stderr
    for line in ("[OK  ] system", "[OK  ] packages", "[OK  ] text encoding", "[OK  ] folders", "[OK  ] one Minty at a time", "[OK  ] stopping programs", "[OK  ] time zones", "[OK  ] second brain"):
        assert line in done.stdout, done.stdout
    assert "FAIL" not in done.stdout.replace("0 FAIL", "")


def test_the_install_check_really_starts_minty_on_free_ports_and_stops_it():
    done = run_check(timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "[OK  ] starts and answers" in done.stdout and "the panel answered" in done.stdout


def test_the_install_check_says_fail_and_exits_1_when_something_is_broken(tmp_path):
    broken = tmp_path / "sitecustomize.py"
    broken.write_text("import sys\nsys.modules['numpy'] = None\n", encoding="utf-8")  # `import numpy` now fails
    env = dict(os.environ, PYTHONPATH=f"{tmp_path}{os.pathsep}{REPO / 'src'}", PYTHONUTF8="1")
    done = subprocess.run([sys.executable, str(REPO / "scripts" / "install_check.py"), "--quick"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=240, cwd=str(REPO))
    assert done.returncode == 1 and "[FAIL] packages" in done.stdout and "missing: numpy" in done.stdout and "send this whole output" in done.stdout


def test_the_windows_dependency_check_applies_windows_markers_and_leaves_out_the_linux_only_ones():
    import importlib.util

    spec = importlib.util.spec_from_file_location("deps", REPO / "scripts" / "check_windows_deps.py")
    deps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deps)
    base = deps.for_windows(deps.groups()["base"], "3.12")
    assert any(r.startswith("tzdata") for r in base)  # the Windows-only requirement is there
    assert deps.for_windows(deps.groups()["local-llm"], "3.12") == []  # llama-cpp-python has no Windows wheels: left out, not failed
    voice310 = deps.for_windows(deps.groups()["voice"], "3.10")
    assert any(r.startswith("filelock>=4.1.0") for r in voice310) and not any(r.startswith("filelock>=4.2.1") for r in voice310)
    assert all(";" not in r for r in base)  # markers are removed so pip does not evaluate them for this machine


# ---- the guided setup and the tuning on Windows ---------------------------------------------------------------------------------------------
def test_on_windows_a_missing_tool_comes_with_the_winget_command_that_installs_it(monkeypatch):
    from minty_core.system import osinfo

    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    here = osinfo.detect()
    assert here.manager == "winget" and here.pretty.startswith("Windows")
    assert osinfo.hint_for(here, "ffmpeg") == "ffmpeg is not installed. winget install -e --id Gyan.FFmpeg"
    assert osinfo.install_many(here, ["git", "nodejs"]) == "winget install -e --id Git.Git; winget install -e --id OpenJS.NodeJS.LTS"
    assert osinfo.update_command(here) == "winget upgrade --all" and "sudo" not in osinfo.install_command(here, "gh")
    assert osinfo.detect(REPO / "no-such-os-release").manager == "unknown"  # an explicit path is still read as a file


def test_on_linux_the_package_commands_are_unchanged(monkeypatch):
    from minty_core.system import osinfo

    monkeypatch.setattr(osutil, "IS_WINDOWS", False)
    arch = osinfo.OS("arch", "Arch Linux", "pacman")
    assert osinfo.install_command(arch, "ffmpeg") == "sudo pacman -S ffmpeg" and osinfo.update_command(arch) == "sudo pacman -Syu"


def test_a_windows_pc_is_tuned_from_what_windows_says_and_not_left_unknown(monkeypatch, tmp_path):
    from minty_core import hwprobe

    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    monkeypatch.setattr(osutil, "windows_hardware", lambda: {"total_mb": 16000, "available_mb": 9000, "flags": frozenset({"sse2", "avx", "avx2"})})
    monkeypatch.setattr(hwprobe.os, "cpu_count", lambda: 12)
    hw = hwprobe.probe(root=tmp_path, run=lambda cmd: "")  # an empty folder: there is no /proc, as on Windows
    assert (hw.ram_mb, hw.available_mb, hw.avx2, hw.threads) == (16000, 9000, True, 12) and hwprobe.tier(hw) == "t2"
    monkeypatch.setattr(osutil, "windows_hardware", lambda: {})  # Windows could not be asked: unknown memory is the middle tier, never "tiny"
    assert hwprobe.tier(hwprobe.probe(root=tmp_path, run=lambda cmd: "")) == "t1"


def test_the_setup_tells_a_windows_user_what_works_and_looks_for_chrome_where_windows_puts_it(monkeypatch, tmp_path):
    from minty_core.onboard import steps

    assert steps._windows_chrome() is False  # not Windows
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    chrome = tmp_path / "Google" / "Chrome" / "Application"
    chrome.mkdir(parents=True)
    (chrome / "chrome.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "none"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    assert steps._windows_chrome() is True


# ---- what the first real Windows run (GitHub Actions) found --------------------------------------------------------------------------------
def test_the_second_brain_check_survives_a_database_file_that_windows_still_holds_open():
    # Windows cannot delete a file another handle has open (WinError 32), and the check's scratch folder held jobs.sqlite open: the check failed on a cleanup, not on the second brain.
    import inspect
    import importlib.util

    spec = importlib.util.spec_from_file_location("install_check", REPO / "scripts" / "install_check.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    assert "ignore_cleanup_errors=True" in inspect.getsource(check.brain)


def test_the_test_extra_installs_everything_the_tests_import_from_outside_the_standard_library():
    from minty_core._toml import tomllib

    extra = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]["dev"]
    assert any(item.lower().startswith("pyyaml") for item in extra)  # tests/test_docker_files.py and test_release_workflow.py read YAML


def test_each_windows_job_installs_with_the_python_it_is_named_after():
    # install.ps1 picks the newest Python it finds, so the "3.10" job tested 3.13 until it was told which one to use.
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"install\.ps1 -NoOnboard -Python \(Get-Command python\)\.Source", text)


def test_the_installer_shows_what_python_said_when_minty_does_not_start_after_installing():
    # the first 3.10 run on Windows said only "does not start": the reason (no tomllib) was thrown away by `2>$null`
    text = (REPO / "install.ps1").read_text(encoding="ascii")
    assert 'import minty_core.app" 2>$null' not in text
    assert re.search(r'import minty_core\.app" 2>&1 \| Out-String', text) and "What Python said" in text


def test_the_linux_ci_job_installs_the_optional_packages_the_tests_exercise():
    # the first green-field CI run: 131 failures, all "No module named playwright / faster_whisper / cryptography ..." because only `.[dev]` was installed
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    first_job = text.split("windows:")[0]
    for extra in ("voice", "browser", "backup", "documents"):
        assert re.search(r"pip install -e '\.\[[^\]]*\b" + extra + r"\b", first_job), extra
    assert "playwright install chromium" in first_job
