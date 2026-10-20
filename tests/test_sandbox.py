"""Bubblewrap sandbox: nothing runs unsandboxed, the home folder and the network are closed (W1-10)."""
import asyncio
import socket
import sys

import pytest

from minty_core.sandbox import Sandbox, SandboxUnavailable

pytestmark = pytest.mark.skipif(not Sandbox().available()[0], reason="bubblewrap or user namespaces unavailable")


async def test_it_runs_a_command_and_returns_its_output():
    out = await Sandbox().run(["/bin/sh", "-c", "echo hello; echo oops >&2; exit 3"])
    assert out.returncode == 3 and out.stdout.strip() == "hello" and "oops" in out.stderr and not out.timed_out


async def test_the_home_folder_is_not_visible(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "minty").mkdir(parents=True)
    (home / ".config" / "minty" / "secrets.toml").write_text('groq = "gsk-not-a-real-key"\n')
    monkeypatch.setenv("HOME", str(home))
    sb = Sandbox(home=home)
    out = await sb.run(["/bin/sh", "-c", f"cat {home}/.config/minty/secrets.toml; ls {home}/.config"])
    assert "gsk-not-a-real-key" not in out.stdout and out.returncode != 0 or out.stdout.strip() == ""


async def test_an_allowed_folder_is_visible_read_only_and_a_writable_one_is_writable(tmp_path):
    ro, rw = tmp_path / "ro", tmp_path / "rw"
    ro.mkdir()
    rw.mkdir()
    (ro / "a.txt").write_text("readable")
    sb = Sandbox(home=tmp_path)
    out = await sb.run(["/bin/sh", "-c", f"cat {ro}/a.txt; echo x > {ro}/b.txt; echo y > {rw}/c.txt"], ro=[ro], rw=[rw])
    assert "readable" in out.stdout and not (ro / "b.txt").exists() and (rw / "c.txt").read_text() == "y\n"


async def test_the_network_is_closed_unless_allowed():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    code = f"import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', {port})); print('connected')"
    try:
        closed = await Sandbox().run([sys.executable, "-c", code], ro=[sys.prefix], timeout_s=10)
        assert "connected" not in closed.stdout and closed.returncode != 0
    finally:
        server.close()


async def test_a_job_that_runs_too_long_is_killed():
    out = await Sandbox().run(["/bin/sh", "-c", "sleep 30"], timeout_s=0.5)
    assert out.timed_out and out.returncode != 0


async def test_the_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret-value")
    out = await Sandbox().run(["/bin/sh", "-c", "env"])
    assert "gsk-secret-value" not in out.stdout and "GROQ_API_KEY" not in out.stdout


async def test_without_bubblewrap_it_refuses_instead_of_running_unsandboxed():
    sb = Sandbox(bwrap="/nonexistent/bwrap")
    assert sb.available()[0] is False
    with pytest.raises(SandboxUnavailable):
        await sb.run(["/bin/echo", "hi"])


async def test_output_is_cut_at_the_limit():
    out = await Sandbox().run(["/bin/sh", "-c", "yes | head -c 500000"], max_output=1000)
    assert len(out.stdout) <= 1100 and out.truncated
    assert asyncio.get_running_loop() is not None
