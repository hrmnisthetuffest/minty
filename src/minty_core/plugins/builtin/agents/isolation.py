"""Run a coding agent, and the tests it wrote, inside bubblewrap.

The agent gets its project folder, its own sign-in folder and the network it needs to reach its model. The rest of the home folder
(Minty's keys, SSH keys, browser profiles) is not there. The project's tests, which run the agent's code, get the project folder and
no network at all. Modes:

  auto      sandbox whenever it can be built; when it cannot, run as before and say so in the result
  required  never run an agent without it
  off       the user's choice; also reported
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from minty_core.sandbox import SAFE_PATH, Sandbox, SandboxUnavailable

log = logging.getLogger(__name__)
MODES = ("auto", "required", "off")


class Isolation:
    def __init__(self, mode: str = "auto", sandbox=None, home: Path | None = None):
        self.mode = mode if mode in MODES else "auto"
        self._home = Path(home) if home is not None else Path.home()
        self._box = sandbox if sandbox is not None else Sandbox(home=self._home)

    def prepare(self) -> tuple[bool, str]:
        """(sandbox in use, the reason when it is not). Raises SandboxUnavailable when it is required and cannot be built."""
        if self.mode == "off":
            return False, "switched off in the settings"
        ok, why = self._box.available()
        if ok:
            return True, ""
        if self.mode == "required":
            raise SandboxUnavailable(why)
        log.warning("coding agent runs without a sandbox: %s", why)
        return False, why

    def _existing(self, names) -> list[Path]:
        return [self._home / n for n in names if (self._home / n).exists()]

    def _program_dirs(self, binary: str) -> list[Path]:
        """Where the agent's program lives, when that is inside the home folder (and so hidden by default)."""
        found = shutil.which(binary)
        if not found:
            return []
        out = []
        for p in {Path(found).parent, Path(found).resolve().parent}:
            try:
                p.relative_to(self._home)
            except ValueError:
                continue
            out.append(p)
        return sorted(out)

    def agent_command(self, argv, project: Path, adapter, env: dict) -> list[str]:
        project = Path(project).resolve()
        ro = self._existing(getattr(adapter, "home_ro", ()))
        ro += [p for p in self._program_dirs(getattr(adapter, "binary", "")) if p not in ro]
        rw = [project, *self._existing(getattr(adapter, "home_rw", ()))]
        for name in getattr(adapter, "env_dirs", ()):
            folder = env.get(name)
            if folder and Path(folder).is_dir():
                rw.append(Path(folder))
        return self._box.command(argv, network=True, ro=ro, rw=rw, cwd=project, env=env)

    def check_command(self, argv, project: Path) -> list[str]:
        project = Path(project).resolve()
        return self._box.command(argv, network=False, rw=[project], cwd=project)

    def check_blocked(self, argv, project: Path) -> str:
        """Why the project's test program would not start in the sandbox, decided before anything runs ('' when it would). Decided here and
        not from what the test prints: the tests are code the agent may have written, and what they print is theirs to choose."""
        program = str(argv[0])
        project = Path(project).resolve()
        if "/" not in program:
            return "" if shutil.which(program, path=SAFE_PATH) else f"{program} is not in {SAFE_PATH}"
        path = Path(program)
        resolved = (path if path.is_absolute() else project / path).resolve()
        for folder in (project,):
            if resolved == folder or folder in resolved.parents:
                return ""
        if self._home == resolved or self._home in resolved.parents:
            return f"{resolved.name} is in your home folder, which the sandbox hides"
        return "" if resolved.exists() else f"{resolved.name} does not exist"
