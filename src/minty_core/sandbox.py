"""Run a command inside a bubblewrap sandbox: read-only system, the home folder hidden, a private /tmp, no network
unless allowed, a scrubbed environment and limits on time, memory, CPU and output.

It refuses rather than run unsandboxed: no bubblewrap, or user namespaces switched off, raises SandboxUnavailable.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import resource
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"


class SandboxUnavailable(RuntimeError):
    """The sandbox cannot be built here; nothing is run."""


@dataclass
class SandboxResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    truncated: bool = False


class Sandbox:
    def __init__(self, bwrap: str = "bwrap", home: Path | None = None):
        self._bwrap = bwrap
        self._home = Path(home) if home is not None else Path.home()
        self._probe: tuple[bool, str] | None = None

    # ---- is it possible here? ------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        """(True, "") or (False, why). Probed once: a real, tiny sandbox must start."""
        if self._probe is None:
            exe = shutil.which(self._bwrap) if os.sep not in self._bwrap else (
                self._bwrap if os.access(self._bwrap, os.X_OK) else None)
            if exe is None:
                self._probe = (False, "bubblewrap (bwrap) is not installed")
            else:
                try:
                    done = subprocess.run([exe, "--unshare-all", "--ro-bind", "/", "/", "--", "/bin/true"],
                                          capture_output=True, timeout=10)
                    ok = done.returncode == 0
                    self._probe = (ok, "" if ok else "user namespaces are not available: "
                                   + done.stderr.decode(errors="replace").strip()[:160])
                except (OSError, subprocess.TimeoutExpired) as e:
                    self._probe = (False, f"bubblewrap could not start: {e}")
        return self._probe

    # ---- building and running ------------------------------------------------------------------
    def command(self, argv: Sequence[str], *, network: bool = False, ro: Sequence[Path] = (),
                rw: Sequence[Path] = (), cwd: Path | None = None, env: Mapping[str, str] | None = None) -> list[str]:
        exe = shutil.which(self._bwrap) or self._bwrap
        args = [exe, "--die-with-parent", "--unshare-all", "--new-session"]
        if network:
            args.append("--share-net")
        args += ["--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--tmpfs", str(self._home)]  # the home folder (keys, config, history) is an empty folder
        for path in ro:
            args += ["--ro-bind", str(path), str(path)]
        for path in rw:
            args += ["--bind", str(path), str(path)]
        if env is None:
            args += ["--clearenv", "--setenv", "HOME", "/tmp", "--setenv", "PATH", SAFE_PATH, "--setenv", "LANG", "C.UTF-8"]  # clear first: a --setenv before it is wiped
        else:  # exactly these variables and no others (a program that must find its own settings via HOME and a login variable)
            args.append("--clearenv")
            for key, value in env.items():
                args += ["--setenv", str(key), str(value)]
        if cwd is not None:
            args += ["--chdir", str(cwd)]
        return [*args, "--", *argv]

    async def run(self, argv: Sequence[str], *, network: bool = False, ro: Sequence[Path] = (),
                  rw: Sequence[Path] = (), cwd: Path | None = None, timeout_s: float = 30.0, mem_mb: int = 1024,
                  max_output: int = 65536) -> SandboxResult:
        ok, why = self.available()
        if not ok:
            raise SandboxUnavailable(why)
        cmd = self.command(argv, network=network, ro=ro, rw=rw, cwd=cwd)

        def limits() -> None:  # runs in the child before exec
            resource.setrlimit(resource.RLIMIT_CPU, (int(timeout_s) + 5, int(timeout_s) + 5))
            if mem_mb:
                resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 2**20, mem_mb * 2**20))

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={"PATH": SAFE_PATH}, preexec_fn=limits, start_new_session=True)
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, 9)
            out, err = await proc.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, 9)
            raise
        text_out = out.decode(errors="replace")
        text_err = err.decode(errors="replace")
        truncated = len(text_out) > max_output or len(text_err) > max_output
        return SandboxResult(proc.returncode if proc.returncode is not None else -9, text_out[:max_output],
                             text_err[:max_output], timed_out, truncated)
