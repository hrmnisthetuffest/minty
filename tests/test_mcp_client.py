"""201: the MCP client, against a real tiny server over stdio."""
import asyncio
import sys
from pathlib import Path

import pytest

from minty_core.mcp.client import McpError, McpSession

SERVER = str(Path(__file__).with_name("fake_mcp_server.py"))


def session(mode="normal", log=None, **kw):
    env = {"PATH": "/usr/bin:/bin"}
    if log:
        env["FAKE_MCP_LOG"] = str(log)
    return McpSession([sys.executable, SERVER, mode], env=env, **kw)


async def test_it_initializes_lists_tools_and_calls_one(tmp_path):
    async with session(log=tmp_path / "log") as s:
        info = await s.initialize()
        assert info["serverInfo"]["name"] == "fake"
        tools = await s.list_tools()
        assert [t["name"] for t in tools] == ["echo", "delete_all"]
        result = await s.call_tool("echo", {"text": "hello"})
    assert result.text == "echo: hello\n[image omitted]" and not result.is_error
    log = (tmp_path / "log").read_text()
    assert "initialized" in log and 'call echo {"text": "hello"}' in log


async def test_a_server_asking_to_use_our_model_is_always_refused(tmp_path):
    async with session("sampling", tmp_path / "log") as s:
        await s.initialize()
        await s.list_tools()
        await asyncio.sleep(0.2)
    log = (tmp_path / "log").read_text()
    assert "sampling-reply" in log and "-32601" in log


async def test_junk_lines_from_the_server_are_ignored():
    async with session("junk") as s:
        await s.initialize()
        assert len(await s.list_tools()) == 2


async def test_a_server_that_never_answers_times_out_and_is_killed():
    s = session("hang", timeout=0.5)
    async with s:
        await s.initialize()
        with pytest.raises(McpError, match="did not answer"):
            await s.call_tool("echo", {"text": "x"})
    assert s.exited()


async def test_a_huge_answer_is_refused_instead_of_filling_memory():
    s = session("huge", max_line=100_000)
    async with s:
        await s.initialize()
        with pytest.raises(McpError, match="too large"):
            await s.call_tool("echo", {"text": "x"})


async def test_output_text_is_capped():
    async with session() as s:
        await s.initialize()
        r = await s.call_tool("echo", {"text": "y" * 20_000})
    assert len(r.text) <= 6000 and r.text.endswith("[cut]")


async def test_the_process_is_gone_after_leaving_the_block():
    s = session()
    async with s:
        await s.initialize()
    assert s.exited()


async def test_a_server_that_cannot_start_is_a_clean_error():
    with pytest.raises(McpError, match="could not start"):
        async with McpSession(["/definitely/not/a/server"], env={}) as s:
            await s.initialize()


async def test_the_environment_is_exactly_what_was_given(tmp_path):
    marker = tmp_path / "env.txt"
    script = tmp_path / "printenv.py"
    script.write_text("import os,sys,json\nopen(sys.argv[1],'w').write(json.dumps(sorted(os.environ)))\n"
                      "for line in sys.stdin:\n    pass\n")
    import os
    os.environ["GROQ_API_KEY"] = "gsk_leak"
    try:
        s = McpSession([sys.executable, str(script), str(marker)], env={"PATH": "/usr/bin:/bin", "FOO": "bar"}, timeout=0.5)
        async with s:
            await asyncio.sleep(0.3)
    finally:
        del os.environ["GROQ_API_KEY"]
    seen = marker.read_text()
    assert "FOO" in seen and "GROQ_API_KEY" not in seen and "HOME" not in seen
