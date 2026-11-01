import asyncio
import logging
import os
import re
import shlex
import shutil
import sys
import time
from importlib import metadata
from pathlib import Path

from minty_core import osutil
from minty_core.plugins.host import Tool
from minty_core.policy import R3
from minty_core.tasks import cancel_and_wait

log = logging.getLogger(__name__)

TIMEOUT_S = 600.0  # kill an install that runs longer than this
GRACE_S = 3.0  # SIGTERM -> SIGKILL grace
TAIL_CHARS = 600
MAX_LEN = 120
DEFAULT_MANAGERS = "pip,pipx,npm"
RUNNABLE = ("pip", "pipx", "npm")
SYSTEM_INSTALL = {
    "apt": "sudo apt install {p}",
    "dnf": "sudo dnf install {p}",
    "pacman": "sudo pacman -S {p}",
    "snap": "sudo snap install {p}",
    "flatpak": "flatpak install flathub {p}",
}
SYSTEM_REMOVE = {
    "apt": "sudo apt remove {p}",
    "dnf": "sudo dnf remove {p}",
    "pacman": "sudo pacman -R {p}",
    "snap": "sudo snap remove {p}",
    "flatpak": "flatpak uninstall {p}",
}
# Never uninstalled: removing these would break Minty itself. critical_packages() adds Minty's own
# dependency closure from the installed metadata on top of this fixed list.
CRITICAL = {
    "minty", "minty-core", "pip", "setuptools", "wheel", "fastapi", "uvicorn", "httpx", "pydantic", "starlette",
    "numpy", "websockets", "tomli", "tomli-w", "anyio", "h11", "httpcore", "certifi", "idna", "pydantic-core",
    "typing-extensions", "click", "sniffio",
}
_REQ_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def critical_packages() -> set[str]:
    """The fixed denylist plus every distribution minty depends on, directly or not (normalised names)."""
    found, pending = set(), ["minty-core"]
    while pending:
        name = pending.pop()
        if _norm(name) in found:
            continue
        found.add(_norm(name))
        try:
            requirements = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            continue
        except Exception:
            log.warning("installer: could not read the requirements of %s", name, exc_info=True)
            continue
        for requirement in requirements:
            match = _REQ_NAME.match(requirement.strip())
            if match:
                pending.append(match.group(0))
    return found | {_norm(n) for n in CRITICAL}

_NAME = r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,99}"
_EXTRAS = r"(\[[A-Za-z0-9,_\-]+\])?"
_VERSION = r"((==|>=|<=|~=)[A-Za-z0-9.*+!\-]+)?"
INSTALL_PIP = re.compile(_NAME + _EXTRAS + _VERSION)
INSTALL_NPM = re.compile(rf"(@{_NAME}/)?{_NAME}{_EXTRAS}{_VERSION}(@[A-Za-z0-9.*+\-]+)?")
BARE_PIP = re.compile(_NAME)
BARE_NPM = re.compile(rf"(@{_NAME}/)?{_NAME}")
_MANAGER = re.compile(r"[a-z0-9_-]{1,20}")


def parse_allowed(value) -> set[str]:
    items = value if isinstance(value, (list, tuple)) else str(value or DEFAULT_MANAGERS).split(",")
    return {str(i).strip().lower() for i in items if str(i).strip()}


def validate(manager: str, package, *, bare: bool) -> str:
    if not isinstance(package, str):
        raise ValueError("The package name must be text.")
    if not package or len(package) > MAX_LEN:
        raise ValueError(f"The package name must be 1 to {MAX_LEN} characters.")
    if package.startswith("-"):
        raise ValueError("The package name can't start with a dash.")
    npm = manager == "npm"
    pattern = (BARE_NPM if npm else BARE_PIP) if bare else (INSTALL_NPM if npm else INSTALL_PIP)
    if not pattern.fullmatch(package):
        what = "a plain package name" if bare else "a package name with an optional version, like requests or requests>=2"
        raise ValueError(f"{package!r} isn't {what}.")
    return package


async def _exited(proc, timeout: float) -> bool:
    """Poll for the direct child's exit (proc.wait() would also wait for the pipe to close)."""
    deadline = time.monotonic() + timeout
    while proc.returncode is None:
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.05)
    return True


async def run_command(argv: list[str]) -> tuple[int | None, str, bool]:
    """Run argv (no shell, own process group). Returns (returncode, output tail, timed_out)."""
    env = dict(os.environ, PIP_NO_INPUT="1")
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, env=env, **osutil.group_kwargs(),
    )
    chunks = bytearray()

    async def read() -> None:
        while data := await proc.stdout.read(65536):
            chunks.extend(data)
            del chunks[:-65536]  # only the tail matters

    reader = asyncio.create_task(read())
    timed_out = False
    try:
        if await _exited(proc, TIMEOUT_S):
            try:
                await asyncio.wait_for(asyncio.shield(reader), 1)
            except asyncio.TimeoutError:  # a grandchild kept the pipe open
                osutil.kill_tree(proc)
        else:
            timed_out = True
            osutil.terminate_tree(proc)
            await _exited(proc, GRACE_S)
            osutil.kill_tree(proc)  # even if the leader exited: stragglers in its group must die
            await _exited(proc, GRACE_S)
    finally:
        if proc.returncode is None:  # cancelled or escalation failed: don't leave it running
            osutil.kill_tree(proc)
        await cancel_and_wait(reader)
    tail = chunks.decode("utf-8", "replace").strip()[-TAIL_CHARS:]
    return proc.returncode, tail, timed_out


def register(ctx):
    allowed = parse_allowed(ctx.settings.get("allowed_managers"))
    lock = asyncio.Lock()  # one install at a time
    protected = critical_packages()

    def build_argv(manager: str, verb: str, package: str) -> list[str] | str:
        """Argv for a runnable manager, or a message when it can't be found."""
        if manager == "pip":
            if verb == "install":
                return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", package]
            return [sys.executable, "-m", "pip", "uninstall", "--yes", "--disable-pip-version-check", package]
        if shutil.which(manager) is None:
            return f"{manager} isn't installed on this machine, so I can't {verb} {package} with it."
        if manager == "pipx":
            return ["pipx", verb, package]
        return ["npm", verb, "--global", "--prefix", str(Path.home() / ".local"), package]

    async def handle(args: dict, verb: str) -> str:
        manager = str(args.get("manager", "")).strip().lower()
        package = validate(manager, args.get("package"), bare=verb == "uninstall" or manager in SYSTEM_INSTALL)
        table = SYSTEM_INSTALL if verb == "install" else SYSTEM_REMOVE
        if manager in table:  # never executed, whatever allowed_managers says
            return (f"I can't {verb} system packages myself. Run this in a terminal: "
                    + table[manager].format(p=shlex.quote(package)))
        if manager not in RUNNABLE:
            shown = manager if _MANAGER.fullmatch(manager) else "that manager"
            return f"I don't know the package manager {shown}. I can use {', '.join(RUNNABLE)}."
        if manager not in allowed:
            return f"Using {manager} isn't allowed in my installer settings."
        if verb == "uninstall" and manager in ("pip", "pipx") and _norm(package) in protected:
            return f"I won't remove {package}: Minty needs it to run."
        argv = build_argv(manager, verb, package)
        if isinstance(argv, str):
            return argv
        async with lock:
            log.info("installer: %s %s package %s", verb, manager, package)
            code, tail, timed_out = await run_command(argv)
        detail = f" Output: {tail}" if tail else ""
        if timed_out:
            return f"The {manager} {verb} of {package} timed out after {int(TIMEOUT_S)} seconds and was stopped.{detail}"
        if code != 0:
            return f"The {manager} {verb} of {package} failed (exit {code}).{detail}"
        done = "Installed" if verb == "install" else "Uninstalled"
        return f"{done} {package} with {manager}.{detail}"

    async def install(args):
        return await handle(args, "install")

    async def uninstall(args):
        return await handle(args, "uninstall")

    schema = {
        "type": "object",
        "properties": {
            "manager": {"type": "string", "description": "pip, pipx or npm (apt, dnf, pacman, flatpak, snap only give instructions)"},
            "package": {"type": "string", "description": "Package name, e.g. requests or requests>=2"},
        },
        "required": ["manager", "package"],
    }

    def describe(verb):
        return lambda a: f"Should I {verb} the {str(a.get('manager', ''))[:20]} package {str(a.get('package', ''))[:MAX_LEN]}?"

    return [
        Tool("install", "Install a package with pip (Minty's own environment), pipx or npm; no root. System managers "
                        "give a command for the user.", schema, install, risky=True,
             risk=R3, describe=describe("install")),  # class 3: it runs code from the internet
        Tool("uninstall", "Remove a package installed with pip, pipx or npm.", schema, uninstall, risky=True,
             describe=describe("uninstall")),
    ]
