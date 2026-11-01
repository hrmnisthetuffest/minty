"""Can every dependency of Minty be installed on Windows? Asks pip, from any system, what it would install for 64-bit Windows, per dependency group and Python version.

    python scripts/check_windows_deps.py                 all groups, Python 3.10, 3.12 and 3.13
    python scripts/check_windows_deps.py voice --python 3.12

Nothing is installed: pip only resolves (`--dry-run --platform win_amd64 --only-binary=:all:`), so a package that would have to be compiled on a PC without a compiler fails here, which is the
point. Each requirement's own marker is evaluated *as Windows* (so `tzdata; sys_platform == 'win32'` is checked, and `llama-cpp-python; sys_platform != 'win32'` is correctly left out).
Limits, so nobody trusts it too far: pip evaluates the markers of the packages those pull in (their dependencies) for the machine it runs on, not for Windows, so a Windows-only dependency of
a dependency is not seen here (the Windows CI job installs for real); and `pynput` is checked on its own because its Linux-only `evdev` would be demanded by a Linux resolver.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
PYTHONS = ("3.10", "3.12", "3.13")
ONLY_BY_ITSELF = {"pynput"}  # resolved with --no-deps (plus six): its Linux-only dependencies are an artifact of resolving on Linux


def windows_env(python: str) -> dict:
    return {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64", "python_version": python,
            "python_full_version": f"{python}.0", "implementation_name": "cpython", "platform_python_implementation": "CPython", "extra": ""}


def groups() -> dict[str, list[str]]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {"base": project["dependencies"], **project.get("optional-dependencies", {})}


def for_windows(requirements: list[str], python: str) -> list[str]:
    """The requirements that apply on Windows with this Python, with their markers removed (pip would evaluate them for this machine otherwise)."""
    keep = []
    for text in requirements:
        req = Requirement(text)
        if req.marker is not None and not req.marker.evaluate(windows_env(python)):
            continue
        keep.append(f"{req.name}{req.specifier}")
    return keep


def resolve(requirements: list[str], python: str, extra: list[str] | None = None) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        command = [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet", "--disable-pip-version-check", "--target", tmp, "--platform", "win_amd64",
                   "--python-version", python, "--only-binary=:all:", *(extra or []), *requirements]
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    lines = [ln for ln in (done.stderr or done.stdout).splitlines() if ln.startswith("ERROR") and "Ignored the following" not in ln]
    return done.returncode == 0, (lines[-1] if lines else "")[:200]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("group", nargs="*", help="dependency groups to check (default: all)")
    parser.add_argument("--python", action="append", help="Python version to check (repeatable; default 3.10, 3.12, 3.13)")
    args = parser.parse_args(argv)
    every = groups()
    wanted = args.group or list(every)
    failed = 0
    for name in wanted:
        if name not in every:
            print(f"unknown group {name!r}; groups: {', '.join(every)}")
            return 2
        for python in args.python or PYTHONS:
            reqs = for_windows(every[name], python)
            alone = [r for r in reqs if Requirement(r).name in ONLY_BY_ITSELF]
            normal = [r for r in reqs if r not in alone]
            ok, why = (True, "")
            if normal:
                ok, why = resolve(normal, python)
            if ok and alone:
                ok, why = resolve([*alone, "six"], python, extra=["--no-deps"])
            failed += not ok
            print(f"{'OK  ' if ok else 'FAIL'} {name:10} python {python:5} {why}")
    print(f"\n{failed} group(s) cannot be installed on Windows." if failed else "\nEvery group can be installed on 64-bit Windows.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
