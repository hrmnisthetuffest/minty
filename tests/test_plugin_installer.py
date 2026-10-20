import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import pytest

from tests.plugin_helpers import make_host

BAD = ["requests; rm -rf /", "--index-url=x", "http://x/y.whl", "../x", "a b", "", "-e", "x" * 121, "$(id)", "a|b"]


class FakeStdout:
    def __init__(self, data: bytes, hang: asyncio.Event | None = None):
        self._data, self._hang = data, hang

    async def read(self, n=-1):
        if self._hang is not None:
            await self._hang.wait()
        data, self._data = self._data, b""
        return data


class FakeProc:
    def __init__(self, output=b"", returncode=0, hang=False):
        self.pid = 424242
        self.returncode = None if hang else returncode
        self.release = asyncio.Event()
        self.stdout = FakeStdout(output, self.release if hang else None)


class Spawns(list):
    """Recorded create_subprocess_exec calls; `queue` holds the FakeProcs to hand out."""

    def __init__(self):
        super().__init__()
        self.queue = []


@pytest.fixture
def spawn(monkeypatch):
    calls = Spawns()

    async def fake_exec(*argv, **kwargs):
        calls.append((argv, kwargs))
        return calls.queue.pop(0) if calls.queue else FakeProc(b"ok")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    return calls


def tools(tmp_path, **settings):
    host = make_host(tmp_path, plugins={"installer": {"enabled": True, **settings}})
    return host.get_tool("installer_install"), host.get_tool("installer_uninstall")


def test_tools_are_risky_with_spoken_prompts(tmp_path):
    install, uninstall = tools(tmp_path)
    assert install.risky and uninstall.risky
    assert install.describe({"manager": "pip", "package": "rich"}) == "Should I install the pip package rich?"
    assert uninstall.describe({"manager": "npm", "package": "left-pad"}) == "Should I uninstall the npm package left-pad?"


@pytest.mark.parametrize("bad", BAD)
async def test_bad_names_rejected_before_anything_runs(tmp_path, spawn, bad):
    install, uninstall = tools(tmp_path)
    for tool in (install, uninstall):
        with pytest.raises(ValueError):
            await tool.func({"manager": "pip", "package": bad})
    assert spawn == []


async def test_non_string_package_rejected(tmp_path, spawn):
    install, _ = tools(tmp_path)
    for bad in (None, 5, ["rich"], {"a": 1}):
        with pytest.raises(ValueError):
            await install.func({"manager": "pip", "package": bad})
    assert spawn == []


async def test_validation_runs_before_manager_and_path_lookup(tmp_path, spawn, monkeypatch):
    looked = []
    monkeypatch.setattr("shutil.which", lambda n: looked.append(n))
    install, _ = tools(tmp_path)
    for manager in ("pipx", "npm", "apt", "nope"):
        with pytest.raises(ValueError):
            await install.func({"manager": manager, "package": "--bad"})
    assert looked == [] and spawn == []


async def test_pip_argv_is_exact(tmp_path, spawn, caplog):
    install, _ = tools(tmp_path)
    spawn.queue.append(FakeProc(b"Successfully installed rich-13.0\n"))
    with caplog.at_level(logging.INFO):
        out = await install.func({"manager": "pip", "package": "rich>=13.0"})
    (argv, kwargs), = spawn
    assert list(argv) == [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "rich>=13.0"]
    assert kwargs["stdin"] == asyncio.subprocess.DEVNULL and kwargs["start_new_session"] is True
    assert kwargs["env"]["PIP_NO_INPUT"] == "1"
    assert "Installed" in out and "Successfully installed rich-13.0" in out
    assert any("pip" in r.getMessage() and "rich>=13.0" in r.getMessage() for r in caplog.records)


async def test_pipx_and_npm_argv(tmp_path, spawn):
    install, _ = tools(tmp_path)
    await install.func({"manager": "PIPX", "package": "black"})
    await install.func({"manager": "npm", "package": "@types/node"})
    assert [list(a) for a, _ in spawn] == [
        ["pipx", "install", "black"],
        ["npm", "install", "--global", "--prefix", str(Path.home() / ".local"), "@types/node"],
    ]


async def test_uninstall_argvs(tmp_path, spawn):
    _, uninstall = tools(tmp_path)
    await uninstall.func({"manager": "pip", "package": "cowsay"})
    await uninstall.func({"manager": "pipx", "package": "cowsay"})
    await uninstall.func({"manager": "npm", "package": "left-pad"})
    assert [list(a) for a, _ in spawn] == [
        [sys.executable, "-m", "pip", "uninstall", "--yes", "--disable-pip-version-check", "cowsay"],
        ["pipx", "uninstall", "cowsay"],
        ["npm", "uninstall", "--global", "--prefix", str(Path.home() / ".local"), "left-pad"],
    ]


@pytest.mark.parametrize("spec", ["rich==13.0", "rich>=1", "rich[extra]", "rich;ls"])
async def test_uninstall_takes_bare_names_only(tmp_path, spawn, spec):
    _, uninstall = tools(tmp_path)
    with pytest.raises(ValueError):
        await uninstall.func({"manager": "pip", "package": spec})
    assert spawn == []


@pytest.mark.parametrize("name", ["pip", "Minty", "minty_core", "MINTY-CORE", "setuptools", "wheel",
                                  "fastapi", "uvicorn", "httpx", "pydantic", "Starlette"])
async def test_critical_packages_not_uninstalled(tmp_path, spawn, name):
    _, uninstall = tools(tmp_path)
    out = await uninstall.func({"manager": "pip", "package": name})
    assert "won't remove" in out and spawn == []


async def test_system_managers_never_exec(tmp_path, spawn):
    install, uninstall = tools(tmp_path, allowed_managers="pip,pipx,npm,apt,flatpak")
    out = await install.func({"manager": "apt", "package": "htop"})
    assert "I can't install system packages myself. Run this in a terminal: sudo apt install htop" in out
    out = await install.func({"manager": "flatpak", "package": "org.gimp.GIMP"})
    assert "flatpak install flathub org.gimp.GIMP" in out and "sudo" not in out
    out = await uninstall.func({"manager": "apt", "package": "htop"})
    assert "sudo apt remove htop" in out
    for m in ("dnf", "pacman", "snap"):
        assert "terminal" in await install.func({"manager": m, "package": "htop"})
    assert spawn == []


async def test_disallowed_and_unknown_managers_refused(tmp_path, spawn):
    install, _ = tools(tmp_path, allowed_managers=" PIP , ")
    assert "isn't allowed" in await install.func({"manager": "npm", "package": "left-pad"})
    assert "don't know" in await install.func({"manager": "brew", "package": "wget"})
    assert "don't know" in await install.func({"manager": "pip; ls", "package": "wget"})
    assert spawn == []
    await install.func({"manager": "pip", "package": "rich"})
    assert len(spawn) == 1


async def test_manager_missing_from_path(tmp_path, spawn, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    install, uninstall = tools(tmp_path)
    assert "pipx isn't installed" in await install.func({"manager": "pipx", "package": "black"})
    assert "npm isn't installed" in await uninstall.func({"manager": "npm", "package": "left-pad"})
    assert spawn == []


async def test_failure_returns_exit_code_and_output_tail(tmp_path, spawn):
    install, _ = tools(tmp_path)
    spawn.queue.append(FakeProc(b"x" * 5000 + b"ERROR: no matching distribution", returncode=1))
    out = await install.func({"manager": "pip", "package": "nosuchpkg"})
    assert "failed" in out and "exit 1" in out and out.endswith("no matching distribution")
    assert len(out) < 900


async def test_timeout_kills_process_group(tmp_path, spawn, monkeypatch):
    install, _ = tools(tmp_path)
    proc = FakeProc(b"", hang=True)
    spawn.queue.append(proc)
    sent = []

    def fake_killpg(pgid, sig):
        sent.append((pgid, sig))
        proc.returncode = -sig
        proc.release.set()

    monkeypatch.setattr(os, "killpg", fake_killpg)
    monkeypatch.setitem(install.func.__globals__, "TIMEOUT_S", 0.1)
    out = await install.func({"manager": "pip", "package": "slowpkg"})
    assert sent == [(424242, signal.SIGTERM), (424242, signal.SIGKILL)]  # SIGKILL even though the leader exited
    assert "timed out" in out


async def test_timeout_escalates_to_sigkill(tmp_path, spawn, monkeypatch):
    install, _ = tools(tmp_path)
    proc = FakeProc(b"", hang=True)
    spawn.queue.append(proc)
    sent = []

    def fake_killpg(pgid, sig):
        sent.append(sig)
        if sig == signal.SIGKILL:
            proc.returncode = -9
            proc.release.set()

    monkeypatch.setattr(os, "killpg", fake_killpg)
    monkeypatch.setitem(install.func.__globals__, "TIMEOUT_S", 0.1)
    monkeypatch.setitem(install.func.__globals__, "GRACE_S", 0.1)
    await install.func({"manager": "pip", "package": "stubborn"})
    assert sent[0] == signal.SIGTERM and signal.SIGKILL in sent


async def test_one_install_at_a_time(tmp_path, spawn, monkeypatch):
    install, _ = tools(tmp_path)
    first = FakeProc(b"one", hang=True)
    spawn.queue.extend([first, FakeProc(b"two")])
    t1 = asyncio.create_task(install.func({"manager": "pip", "package": "one"}))
    await asyncio.sleep(0.1)
    t2 = asyncio.create_task(install.func({"manager": "pip", "package": "two"}))
    await asyncio.sleep(0.2)
    assert len(spawn) == 1 and not t2.done()  # second waits for the first
    first.returncode = 0
    first.release.set()
    assert "Installed" in await t1 and "Installed" in await t2
    assert len(spawn) == 2


# ---- denylist from the dependency closure ----

def _fake_requires(table):
    from importlib import metadata

    def requires(name):
        key = name.lower().replace("_", "-")
        if key not in table:
            raise metadata.PackageNotFoundError(name)
        return table[key]

    return requires


async def test_denylist_follows_minty_core_dependency_closure(tmp_path, spawn, monkeypatch):
    from importlib import metadata

    monkeypatch.setattr(metadata, "requires", _fake_requires({
        "minty-core": ["Fake_Dep>=1.0", "other[x]>=2; extra == 'voice'"], "fake-dep": ["sub.dep (>=1)"],
        "other": None, "sub-dep": []}))
    _, uninstall = tools(tmp_path)
    for name in ("fake_dep", "Fake-Dep", "other", "sub.dep", "numpy", "websockets", "tomli-w", "anyio", "h11",
                 "httpcore", "certifi", "idna", "pydantic_core", "typing_extensions", "click", "sniffio", "tomli"):
        assert "won't remove" in await uninstall.func({"manager": "pip", "package": name}), name
    assert spawn == []
    assert "Uninstalled" in await uninstall.func({"manager": "pip", "package": "cowsay"})


async def test_denylist_falls_back_to_hand_list_without_metadata(tmp_path, spawn, monkeypatch):
    from importlib import metadata

    monkeypatch.setattr(metadata, "requires", _fake_requires({}))
    _, uninstall = tools(tmp_path)
    assert "won't remove" in await uninstall.func({"manager": "pip", "package": "httpx"})
    assert "won't remove" in await uninstall.func({"manager": "pip", "package": "numpy"})
    assert spawn == []


@pytest.mark.parametrize("name", ["minty", "minty-core", "Minty_Core", "httpx", "numpy"])
async def test_pipx_uninstall_honours_the_denylist(tmp_path, spawn, name):
    _, uninstall = tools(tmp_path)
    assert "won't remove" in await uninstall.func({"manager": "pipx", "package": name})
    assert spawn == []


async def test_system_manager_names_are_bare(tmp_path, spawn):
    install, uninstall = tools(tmp_path)
    for tool in (install, uninstall):
        for bad in ("htop>=2", "htop==1.0", "htop[x]", "htop;ls"):
            with pytest.raises(ValueError):
                await tool.func({"manager": "apt", "package": bad})
    out = await install.func({"manager": "apt", "package": "libstdc++6"})
    assert "sudo apt install libstdc++6" in out
    assert "sudo apt install htop" in await install.func({"manager": "apt", "package": "htop"})
    assert spawn == []
