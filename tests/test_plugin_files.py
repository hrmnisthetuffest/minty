import pytest

from tests.plugin_helpers import make_host
from minty_core.safefetch import FetchFailed, Fetched


def host_for(tmp_path, **settings):
    folder = tmp_path / "ws"
    host = make_host(tmp_path, plugins={"files": {"enabled": True, "folder": str(folder), **settings}})
    return host, folder


async def test_write_read_list_move_and_trash(tmp_path):
    host, ws = host_for(tmp_path)
    assert await host.call("files_write", {"path": "notes/todo.txt", "content": "buy milk"}) == "Saved notes/todo.txt."
    assert (ws / "notes" / "todo.txt").read_text() == "buy milk"
    assert "buy milk" in await host.call("files_read", {"path": "notes/todo.txt"})
    assert "todo.txt  (8 B)" in await host.call("files_list", {"path": "notes"})
    assert (await host.call("files_move", {"source": "notes/todo.txt", "destination": "done.txt"})) == "Moved to done.txt."
    assert "trash" in await host.call("files_delete", {"path": "done.txt"})
    assert not (ws / "done.txt").exists() and any((ws / ".trash").iterdir())


async def test_no_silent_overwrite(tmp_path):
    host, ws = host_for(tmp_path)
    await host.call("files_write", {"path": "a.txt", "content": "one"})
    assert (await host.call("files_write", {"path": "a.txt", "content": "two"})).startswith("Error")
    assert (ws / "a.txt").read_text() == "one"
    await host.call("files_write", {"path": "a.txt", "content": "two", "overwrite": True})
    assert (ws / "a.txt").read_text() == "two"


@pytest.mark.parametrize("path", ["../secret.txt", "/etc/passwd", "~/x", "a/../../b", "a\x00b"])
async def test_paths_cannot_leave_the_workspace(tmp_path, path):
    host, _ = host_for(tmp_path)
    for tool, extra in (("files_read", {}), ("files_write", {"content": "x"}), ("files_list", {})):
        result = await host.call(tool, {"path": path, **extra})
        assert result.startswith("Error"), (tool, result)


async def test_symlink_pointing_out_is_refused(tmp_path):
    host, ws = host_for(tmp_path)
    (tmp_path / "outside.txt").write_text("secret")
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "link.txt").symlink_to(tmp_path / "outside.txt")
    result = await host.call("files_read", {"path": "link.txt"})
    assert "secret" not in result


def patch_fetch(host, monkeypatch, fake):
    monkeypatch.setitem(host.get_tool("files_download").func.__globals__, "safe_fetch", fake)


async def test_download_saves_into_workspace_with_safe_name(tmp_path, monkeypatch):
    host, ws = host_for(tmp_path)
    seen = {}

    async def fake(method, url, **kw):
        seen.update(method=method, url=url, **kw)
        return Fetched(200, "application/pdf", b"%PDF-1", "utf-8")

    patch_fetch(host, monkeypatch, fake)
    out = await host.call("files_download", {"url": "https://example.com/files/My%20Report.pdf"})
    assert out == "Downloaded 6 B to My Report.pdf."
    assert (ws / "My Report.pdf").read_bytes() == b"%PDF-1"
    out2 = await host.call("files_download", {"url": "https://example.com/x/../../evil", "name": "../../etc/x"})
    assert (ws / "x").exists() and ".." not in out2
    assert seen["max_bytes"] == 200_000_000


async def test_download_errors_are_clean(tmp_path, monkeypatch):
    host, ws = host_for(tmp_path)

    async def blocked(method, url, **kw):
        raise ValueError("that address is private or local")

    patch_fetch(host, monkeypatch, blocked)
    assert (await host.call("files_download", {"url": "http://127.0.0.1/x"})).startswith("Error: that address")

    async def too_big(method, url, **kw):
        return Fetched(200, "x", b"x", "utf-8", truncated=True)

    patch_fetch(host, monkeypatch, too_big)
    assert "larger than" in await host.call("files_download", {"url": "https://example.com/big"})
    assert list(ws.glob("*")) == []


async def test_upload_and_delete_are_risky_with_clear_prompts(tmp_path):
    host, _ = host_for(tmp_path)
    up, rm = host.get_tool("files_upload"), host.get_tool("files_delete")
    assert up.risky and rm.risky
    assert up.describe({"path": "report.pdf", "url": "https://files.example.com/in"}) == \
        "Should I upload report.pdf to files.example.com?"
    assert rm.describe({"path": "old/notes.txt"}) == "Should I delete notes.txt?"
    for name in ("files_list", "files_write", "files_download", "files_move"):
        assert not host.get_tool(name).risky
    assert host.get_tool("files_read").sensitive


async def test_agents_can_start_without_asking_when_enabled(tmp_path):
    ask = make_host(tmp_path / "a", plugins={"agents": {"enabled": True}})
    auto = make_host(tmp_path / "b", plugins={"agents": {"enabled": True, "auto_start": True}})
    assert ask.get_tool("agents_start_task").risky and ask.get_tool("agents_start_project").risky
    assert not auto.get_tool("agents_start_task").risky and not auto.get_tool("agents_start_project").risky
