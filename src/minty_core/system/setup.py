"""The setup-script generator (643). It writes a bash script for THIS system's package manager and never runs it. Every name
is checked against a strict pattern before it can reach the script, and the script asks before doing anything."""
from __future__ import annotations

import re
import shlex

from minty_core.system.osinfo import ARCH_ONLY, OS, install_many
from minty_core.system.triage import valid_unit

_PKG = re.compile(r"[a-z0-9][a-z0-9+._-]{0,60}")
_PIPX = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,60}")
_SEG = re.compile(r"[A-Za-z0-9._ -]{1,60}")


def _directory(raw: str) -> str:
    text = str(raw).strip()
    text = text[2:] if text.startswith("~/") else text
    if text.startswith(("/", "~")) or not text:
        raise ValueError(f"{raw!r} is not a folder under your home.")
    segments = text.split("/")
    if any(seg in ("", ".", "..") or not _SEG.fullmatch(seg) for seg in segments):
        raise ValueError(f"{raw!r} is not a plain folder name.")
    return '"$HOME/' + "/".join(segments) + '"'


def make_script(os_: OS, packages=(), pipx=(), services=(), directories=(), when: str = "") -> str:
    if os_.manager not in ("pacman", "apt"):
        raise ValueError("I do not know this system's package manager, so I cannot write a script for it.")
    for name in packages:
        if not _PKG.fullmatch(str(name)):
            raise ValueError(f"{name!r} is not a package name.")
    for name in pipx:
        if not _PIPX.fullmatch(str(name)):
            raise ValueError(f"{name!r} is not a pipx package name.")
    units = [valid_unit(u) for u in services]
    dirs = [_directory(d) for d in directories]
    if not (packages or pipx or units or dirs):
        raise ValueError("There is nothing to put in the script.")
    lines = ["#!/usr/bin/env bash",
             f"# Setup script for {os_.pretty}, written by Minty on {when}. Nothing has been run.",
             "# Read it through, then run it yourself:  bash this-file.sh", "set -euo pipefail", ""]
    needed, skipped = [], []
    for name in packages:
        (skipped if name in ARCH_ONLY and os_.manager != "pacman" else needed).append(name)
    summary = []
    if needed:
        summary.append(f"install {len(needed)} package{'s' * (len(needed) != 1)}")
    if pipx:
        summary.append(f"install {len(pipx)} program{'s' * (len(pipx) != 1)} with pipx")
    if units:
        summary.append(f"turn on {len(units)} service{'s' * (len(units) != 1)}")
    if dirs:
        summary.append(f"make {len(dirs)} folder{'s' * (len(dirs) != 1)}")
    for name in skipped:
        lines.append(f"# {name} is not needed on {os_.pretty}, so it is left out.")
    lines += [f"echo {shlex.quote('This will: ' + (', '.join(summary) or 'do nothing') + '.')}",
              'read -rp "Carry on? [y/N] " answer', '[[ "$answer" == [yY]* ]] || { echo "Cancelled."; exit 1; }', ""]
    if needed:
        lines.append(install_many(os_, needed))
    for name in pipx:
        lines.append(f"pipx install {name}")
    for unit in units:
        lines.append(f"sudo systemctl enable --now {unit}")
    for d in dirs:
        lines.append(f"mkdir -p {d}")
    lines.append('echo "Done."')
    return "\n".join(lines) + "\n"
