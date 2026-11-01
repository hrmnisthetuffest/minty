"""What Linux can prove about Windows: the source never reaches for a POSIX-only name outside the OS layer, always says which encoding it reads and writes, and imports (and starts)
with every POSIX-only module missing, which is exactly what Windows is. None of this replaces running on Windows: the Windows CI job and `scripts/windows_check.ps1` do that."""
import ast
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

POSIX_ATTRS = {("os", n) for n in ("killpg", "getpgid", "setsid", "fchmod", "fork", "getuid", "geteuid", "getgid", "pread", "pwrite", "chown", "mkfifo", "uname", "getlogin",
                                   "O_NONBLOCK", "O_NOFOLLOW", "O_DIRECTORY", "O_NOCTTY", "O_CLOEXEC")} | \
              {("signal", n) for n in ("SIGKILL", "SIGHUP", "SIGUSR1", "SIGUSR2", "SIGALRM", "SIGSTOP", "SIGCONT", "SIGQUIT", "SIGPIPE")}
POSIX_KEYWORDS = {"preexec_fn", "start_new_session"}
POSIX_MODULES = {"fcntl", "termios", "tty", "pty", "resource", "pwd", "grp", "syslog"}
# Where a POSIX-only name is allowed, and why. Everything else must go through minty_core/osutil.py.
ALLOWED = {
    "osutil.py": "the OS layer itself",
    "instance.py": "os.fchmod behind hasattr(os, 'fchmod'); the lock's Windows branch is in the same file",
    "logredact.py": "os.fchmod behind hasattr(os, 'fchmod')",
    "gfx.py": "os.O_NOCTTY, reached only after the termios check (the terminal is not questioned on Windows)",
    "sandbox.py": "preexec_fn=osutil.limits_preexec(...), which is None on Windows (the sandbox itself is refused there)",
}


def source_files():
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def posix_uses(source: str) -> list[str]:
    tree = ast.parse(source)
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and (n.value.id, n.attr) in POSIX_ATTRS:
            found.append(f"{n.value.id}.{n.attr} (line {n.lineno})")
        elif isinstance(n, ast.keyword) and n.arg in POSIX_KEYWORDS:
            found.append(f"{n.arg}= (line {n.value.lineno})")
    return found


def toplevel_posix_imports(source: str) -> list[str]:
    found = []
    for n in ast.parse(source).body:  # only statements directly in the module: an import inside a function or a try block is a guarded one
        names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
        found += [f"import {name} (line {n.lineno})" for name in names if name.split(".")[0] in POSIX_MODULES]
    return found


TEXT_MODES = {"r", "w", "a", "r+", "w+", "a+", "rt", "wt", "at"}
NOT_A_PATH = {"os", "webbrowser", "Image", "tarfile", "zipfile", "wave", "av", "sqlite3", "gzip", "shelve"}  # `x.open(...)` that is not Path.open


def missing_encoding(source: str) -> list[str]:
    """Text files opened (open, Path.open, read_text, write_text) and text subprocess output decoded without naming the encoding: on Windows that is the legacy code page."""
    found = []
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, ast.Call):
            continue
        kw = {k.arg: k.value for k in n.keywords}
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        if name == "read_text" and n.args and "encoding" in kw:
            found.append(f"read_text() with the encoding twice, positionally and by name (line {n.lineno})")  # a TypeError at run time
            continue
        if "encoding" in kw or any(k.arg is None for k in n.keywords):
            continue
        if name == "read_text" and n.args:
            continue  # read_text("utf-8"): the encoding is the first argument
        if name in ("read_text", "write_text"):
            found.append(f"{name}() (line {n.lineno})")
        elif name == "open":
            mode = kw.get("mode").value if isinstance(kw.get("mode"), ast.Constant) else None
            if mode is None and isinstance(f, ast.Name) and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant):
                mode = n.args[1].value
            if mode is None and isinstance(f, ast.Attribute) and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                mode = n.args[0].value  # path.open("r")
            builtin_text = isinstance(f, ast.Name) and (mode is None or "b" not in str(mode))
            path_text = isinstance(f, ast.Attribute) and str(mode) in TEXT_MODES and not (isinstance(f.value, ast.Name) and f.value.id in NOT_A_PATH)
            if builtin_text or path_text:
                found.append(f"open({mode!r}) (line {n.lineno})")
        elif name in ("run", "Popen", "check_output", "check_call", "call") and ("text" in kw or "universal_newlines" in kw):
            found.append(f"{name}(text=True) (line {n.lineno})")
    return found


# ---- the linters are tested on bad source first, so they cannot pass by finding nothing -----------------------------------------------------
def test_the_posix_lint_sees_what_it_is_meant_to_see():
    bad = textwrap.dedent("""
        import os, signal, subprocess
        os.killpg(1, signal.SIGKILL)
        subprocess.Popen(["x"], start_new_session=True, preexec_fn=lambda: None)
        fd = os.open("f", os.O_NONBLOCK)
    """)
    assert len(posix_uses(bad)) == 5
    assert toplevel_posix_imports("import fcntl\nimport os\n") == ["import fcntl (line 1)"]
    assert toplevel_posix_imports("try:\n    import termios\nexcept ImportError:\n    termios = None\ndef f():\n    import resource\n") == []  # guarded or inside a function: fine


def test_the_encoding_lint_sees_what_it_is_meant_to_see():
    bad = textwrap.dedent("""
        from pathlib import Path
        import subprocess
        Path("a").read_text()
        Path("a").write_text("x")
        open("a")
        open("a", "w")
        Path("a").open("r")
        subprocess.run(["x"], capture_output=True, text=True)
    """)
    assert len(missing_encoding(bad)) == 6
    assert len(missing_encoding('from pathlib import Path\nPath("a").read_text("utf-8", encoding="utf-8")\n')) == 1  # passing it twice is a bug of its own
    assert missing_encoding('from pathlib import Path\nPath("a").read_text("utf-8")\n') == []
    good = textwrap.dedent("""
        from pathlib import Path
        import subprocess, os, webbrowser
        Path("a").read_text(encoding="utf-8")
        open("a", "rb")
        Path("a").open("rb")
        os.open("a", os.O_RDONLY)
        webbrowser.open("https://example.test")
        subprocess.run(["x"], capture_output=True, text=True, encoding="utf-8")
        subprocess.run(["x"], capture_output=True)
    """)
    assert missing_encoding(good) == []


# ---- the source tree ----------------------------------------------------------------------------------------------------------------------
def test_posix_only_names_are_used_only_in_the_os_layer_or_the_few_guarded_places():
    offenders = {}
    for path in source_files():
        if path.name in ALLOWED:
            continue
        found = posix_uses(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path.relative_to(SRC))] = found
    assert not offenders, f"use minty_core/osutil.py (group_kwargs, kill_tree, limits_preexec, ...) instead: {offenders}"


def test_posix_only_modules_are_never_imported_at_the_top_level_of_a_module():
    offenders = {}
    for path in source_files():
        found = toplevel_posix_imports(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path.relative_to(SRC))] = found
    assert not offenders, f"import them inside the function that needs them, or in a try/except ImportError: {offenders}"


def test_every_text_file_and_every_text_subprocess_names_its_encoding():
    offenders = {}
    for path in source_files():
        found = missing_encoding(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path.relative_to(SRC))] = found
    assert not offenders, f"add encoding='utf-8' (and errors='replace' for subprocess output): {offenders}"


def test_the_allowed_places_still_exist():
    for name in ALLOWED:
        assert any(p.name == name for p in source_files()), name


# ---- being Windows: the modules Windows lacks are simply not there ------------------------------------------------------------------------
BLOCK = "for m in ('fcntl', 'termios', 'tty', 'pty', 'resource', 'pwd', 'grp', 'syslog'):\n    sys.modules[m] = None  # `import fcntl` now raises ImportError, as on Windows\n"


def run_python(code: str, tmp_path, timeout: int = 180):
    env = dict(os.environ, PYTHONPATH=str(SRC), MINTY_CONFIG_DIR=str(tmp_path / "cfg"), HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout, cwd=str(tmp_path))


def test_every_module_imports_with_the_posix_only_modules_missing(tmp_path):
    code = BLOCK + textwrap.dedent("""
        import importlib, pkgutil
        import minty_core, minty_panel
        failed = []
        for pkg in (minty_core, minty_panel):
            for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + ".", onerror=lambda n: failed.append(n)):
                if info.name.endswith(".__main__"):
                    continue  # importing it runs the application
                try:
                    importlib.import_module(info.name)
                except Exception as e:
                    failed.append(f"{info.name}: {type(e).__name__}: {e}")
        print("\\n".join(failed))
        sys.exit(1 if failed else 0)
    """)
    done = run_python("import sys\n" + code, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_application_builds_and_the_command_line_starts_with_the_posix_only_modules_missing(tmp_path):
    code = "import sys\n" + BLOCK + textwrap.dedent("""
        from pathlib import Path
        from minty_core.context import build_context
        from minty_core import app
        ctx = build_context(Path(sys.argv[0] if False else "cfg"))
        assert ctx.services["knowledge"] is not None and ctx.services["world"] is not None
        try:
            app.main(["--help"])
        except SystemExit as e:
            assert e.code == 0
        print("started")
    """)
    done = run_python(code, tmp_path)
    assert done.returncode == 0 and "started" in done.stdout, done.stdout + done.stderr


def test_the_whole_test_suite_can_be_collected_with_the_posix_only_modules_missing(tmp_path):
    # On Windows a test file that does `import fcntl` (or pty, termios, resource) at its top is an error that stops the whole run before one test has started.
    code = "import sys\n" + BLOCK + "import pytest\nraise SystemExit(pytest.main(['--collect-only', '-q', '-p', 'no:cacheprovider', str(" + repr(str(ROOT / "tests")) + ")]))\n"
    done = run_python(code, tmp_path, timeout=600)
    assert done.returncode == 0, "\n".join(line for line in (done.stdout + done.stderr).splitlines() if "ERROR" in line or "Error" in line)[-3000:]
