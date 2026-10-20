"""654 plugin template + validator, 655 auto-generated plugin docs."""
import asyncio
import textwrap
from pathlib import Path

import pytest

from minty_core.config import ConfigStore
from minty_core.dev.plugin_kit import plugin_docs, scaffold, validate
from minty_core.keystore import SecretsStore
from minty_core.plugins.host import BUILTIN_PLUGIN_DIR, PluginHost


def write_plugin(root, name, py, toml=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.toml").write_text(toml or f'name = "{name}"\ndescription = "A test plugin."\ndefault_enabled = true\n')
    (d / "plugin.py").write_text(textwrap.dedent(py))
    return d


def messages(problems):
    return [f"{p.level}: {p.message}" for p in problems]


GOOD = '''
    from minty_core.plugins.host import Tool

    def register(ctx):
        async def hi(args):
            return "hi"
        return [Tool("hi", "Say hi.", {"type": "object", "properties": {}}, hi)]
'''


def test_the_scaffold_passes_the_validator_loads_in_the_real_host_and_works(tmp_path):
    made = scaffold("greeter", tmp_path / "plugins", "Greets people by name.")
    assert {p.name for p in made} == {"__init__.py", "plugin.py", "plugin.toml", "test_greeter.py"}
    assert validate(tmp_path / "plugins" / "greeter") == []
    store = ConfigStore(tmp_path / "cfg")
    store.load()
    host = PluginHost([tmp_path / "plugins"], store, SecretsStore(tmp_path / "cfg"), tmp_path / "cfg")
    host.reload()
    assert host.get_tool("greeter_say") is None  # a new plugin starts switched off until you enable it
    data = store.config.model_dump(mode="json")
    data["plugins"] = {"greeter": {"enabled": True}}
    store.update(data)
    host.reload()
    assert asyncio.run(host.call("greeter_say", {"who": "Alex"})) == "Hello, Alex."
    tool = host.get_tool("greeter_say")
    assert tool.risk == 0 and len(tool.description) <= 250


def test_the_scaffolded_test_runs_green(tmp_path):
    import subprocess
    import sys

    scaffold("greeter", tmp_path / "plugins", "Greets people by name.")
    root = Path(__file__).resolve().parents[1]
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          str(tmp_path / "plugins" / "greeter" / "test_greeter.py")],
                         cwd=tmp_path, env={"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("bad", ["Greeter", "a", "x" * 40, "has space", "../evil", "9lives", "web", "system", "browser"])
def test_bad_or_colliding_names_are_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        scaffold(bad, tmp_path / "plugins")


def test_scaffold_never_overwrites(tmp_path):
    scaffold("greeter", tmp_path / "plugins")
    with pytest.raises(ValueError, match="already exists"):
        scaffold("greeter", tmp_path / "plugins")


def test_a_clean_hand_written_plugin_passes(tmp_path):
    assert validate(write_plugin(tmp_path, "ok", GOOD)) == []


def test_dangerous_calls_are_errors(tmp_path):
    for i, body in enumerate(["eval(args['x'])", "exec('x = 1')", "__import__('os').system('ls')", "os.system('ls')",
                              "subprocess.run('ls', shell=True)", "pickle.loads(b'')"]):
        d = write_plugin(tmp_path, f"bad{i}", f'''
            import os, subprocess, pickle
            from minty_core.plugins.host import Tool
            def register(ctx):
                async def go(args):
                    {body}
                    return "x"
                return [Tool("go", "Go.", {{"type": "object", "properties": {{}}}}, go, risk=2)]
        ''')
        assert any(p.level == "error" for p in validate(d)), body


def test_the_network_must_be_declared_and_go_through_the_shared_client(tmp_path):
    src = '''
        import requests
        from minty_core.plugins.host import Tool
        def register(ctx):
            async def go(args):
                return requests.get("https://x.example").text
            return [Tool("go", "Go.", {"type": "object", "properties": {}}, go)]
    '''
    errs = messages(validate(write_plugin(tmp_path, "net", src)))
    assert any("network" in e and "network = true" in e for e in errs)
    declared = write_plugin(tmp_path, "net2", src, 'name = "net2"\ndescription = "x"\nnetwork = true\n')
    assert not any("network" in m for m in messages(validate(declared)) if m.startswith("error"))


def test_a_plugin_that_changes_things_must_declare_a_risk(tmp_path):
    src = '''
        from pathlib import Path
        from minty_core.plugins.host import Tool
        def register(ctx):
            async def save(args):
                Path("/tmp/x").write_text("hi")
                return "saved"
            return [Tool("save", "Save a file.", {"type": "object", "properties": {}}, save%s)]
    '''
    assert any("risk" in m for m in messages(validate(write_plugin(tmp_path, "p1", src % ""))))
    assert not any(p.level == "error" for p in validate(write_plugin(tmp_path, "p2", src % ", risk=1")))


def test_structure_errors(tmp_path):
    no_register = write_plugin(tmp_path, "noreg", "x = 1\n")
    assert any("register" in m for m in messages(validate(no_register)))
    bad_name = write_plugin(tmp_path, "badtool", GOOD.replace('Tool("hi"', 'Tool("Hi There"'))
    assert any("tool name" in m for m in messages(validate(bad_name)))
    long_desc = write_plugin(tmp_path, "longdesc", GOOD.replace('"Say hi."', '"' + "x" * 300 + '"'))
    assert any("description" in m and "250" in m for m in messages(validate(long_desc)))
    twice = '''
        from minty_core.plugins.host import Tool

        def register(ctx):
            async def hi(args):
                return "hi"
            spec = {"type": "object", "properties": {}}
            return [Tool("hi", "Say hi.", spec, hi), Tool("hi", "Say hi again.", spec, hi)]
    '''
    assert any("twice" in m for m in messages(validate(write_plugin(tmp_path, "dup", twice))))
    wrong = write_plugin(tmp_path, "wrong", GOOD, 'name = "other"\ndescription = "x"\n')
    assert any("name" in m and "folder" in m for m in messages(validate(wrong)))
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "plugin.toml").write_text("name = ")
    (broken / "plugin.py").write_text("def register(ctx): return []")
    assert any("plugin.toml" in m for m in messages(validate(broken)))
    syntax = write_plugin(tmp_path, "syntax", "def register(ctx:\n")
    assert any("syntax" in m.lower() for m in messages(validate(syntax)))


def test_the_docs_cover_every_builtin_tool_the_host_actually_loads(tmp_path):
    store = ConfigStore(tmp_path / "cfg")
    store.load()
    host = PluginHost([BUILTIN_PLUGIN_DIR], store, SecretsStore(tmp_path / "cfg"), tmp_path / "cfg")
    host.reload()
    loaded = {t.name for p in host._plugins.values() for t in p.tools.values()}
    docs = plugin_docs([BUILTIN_PLUGIN_DIR])
    missing = sorted(n for n in loaded if f"`{n}`" not in docs)
    assert len(loaded) > 100 and missing == [], missing
    assert docs.startswith("# Plugins") and "## web" in docs and "system_control" in docs
    assert "sensitive" in docs and "risk" in docs
