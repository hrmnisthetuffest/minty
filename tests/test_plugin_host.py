from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.plugins.host import BUILTIN_PLUGIN_DIR, PluginHost

TOML = 'name = "demo"\ndescription = "Demo"\n[settings.greeting]\ntype = "string"\n'
PY = '''
from minty_core.plugins.host import Tool

def register(ctx):
    def hello(args):
        return f"{ctx.settings.get('greeting', 'hi')} {args.get('who', 'you')}"
    async def ahello(args):
        return "async ok"
    def boom(args):
        raise RuntimeError("kaput")
    obj = {"type": "object", "properties": {}}
    return [Tool("hello", "say hi", obj, hello), Tool("ahello", "async", obj, ahello),
            Tool("boom", "explodes", obj, boom, risky=True)]
'''


def write_plugin(root, name="demo", toml=TOML, py=PY):
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.toml").write_text(toml)
    (d / "plugin.py").write_text(py)


def make_host(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    secrets = SecretsStore(tmp_path)
    return PluginHost([tmp_path / "plugins"], store, secrets, tmp_path), store


async def test_tools_are_namespaced_and_callable(tmp_path):
    write_plugin(tmp_path / "plugins")
    host, _ = make_host(tmp_path)
    host.reload()
    assert {t.name for t in host.tool_specs()} == {"demo_hello", "demo_ahello", "demo_boom"}
    assert await host.call("demo_hello", {"who": "bob"}) == "hi bob"
    assert await host.call("demo_ahello", {}) == "async ok"
    assert host.get_tool("demo_boom").risky is True


async def test_tool_exception_becomes_error_string(tmp_path):
    write_plugin(tmp_path / "plugins")
    host, _ = make_host(tmp_path)
    host.reload()
    assert await host.call("demo_boom", {}) == "Error: RuntimeError: kaput"
    assert (await host.call("nope_nothing", {})).startswith("Error: unknown tool")


async def test_settings_and_disable_come_from_config(tmp_path):
    write_plugin(tmp_path / "plugins")
    host, store = make_host(tmp_path)
    data = store.config.model_dump(mode="json")
    data["plugins"] = {"demo": {"greeting": "yo"}}
    store.update(data)
    host.reload()
    assert await host.call("demo_hello", {"who": "bob"}) == "yo bob"
    data["plugins"] = {"demo": {"enabled": False}}
    store.update(data)
    host.reload()
    assert host.tool_specs() == []
    info = host.list_plugins()[0]
    assert info["name"] == "demo" and info["enabled"] is False
    assert "greeting" in info["settings_schema"]


def test_broken_plugin_does_not_break_others(tmp_path):
    write_plugin(tmp_path / "plugins")
    write_plugin(tmp_path / "plugins", name="bad", toml='name = "bad"\n', py="this is not python")
    write_plugin(tmp_path / "plugins", name="worse", toml='name = "worse"\n',
                 py="from minty_core.plugins.host import Tool\n"
                    "def register(ctx):\n    return [Tool('bad name!', 'x', {}, lambda a: 'x')]\n")
    host, _ = make_host(tmp_path)
    host.reload()
    by_name = {p["name"]: p for p in host.list_plugins()}
    assert by_name["demo"]["error"] is None and by_name["demo"]["tools"]
    assert by_name["bad"]["error"] and by_name["bad"]["tools"] == []
    assert by_name["worse"]["error"] and by_name["worse"]["tools"] == []
    assert any(t.name == "demo_hello" for t in host.tool_specs())


async def test_builtin_plugins_load_and_memory_tool_writes_file(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    host = PluginHost([BUILTIN_PLUGIN_DIR], store, SecretsStore(tmp_path), tmp_path)
    host.reload()
    names = {t.name for t in host.tool_specs()}
    assert {"clock_get_time", "memory_remember"} <= names
    assert "Error" not in await host.call("clock_get_time", {})
    assert await host.call("memory_remember", {"note": "likes tea"}) == "Noted."
    assert (tmp_path / "memory.md").read_text() == "- likes tea\n"


def test_tool_name_with_newline_is_reported_as_error(tmp_path):
    """Test that a tool name containing newline is caught by fullmatch validation."""
    write_plugin(tmp_path / "plugins", name="badname", toml='name = "badname"\n',
                 py="from minty_core.plugins.host import Tool\n"
                    "def register(ctx):\n    return [Tool('ok\\n', 'x', {}, lambda a: 'x')]\n")
    host, _ = make_host(tmp_path)
    host.reload()
    by_name = {p["name"]: p for p in host.list_plugins()}
    assert by_name["badname"]["error"] and by_name["badname"]["tools"] == []
