from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.plugins.host import PluginHost

PLUGIN = '''
from minty_core.plugins.host import Tool

def register(ctx):
    def bump(args):
        ctx.state["n"] = ctx.state.get("n", 0) + 1
        return str(ctx.state["n"])
    def say(args):
        ctx.notify("hello")
        return "ok"
    obj = {"type": "object", "properties": {}}
    return [Tool("bump", "count", obj, bump), Tool("say", "notify", obj, say)]
'''


async def test_state_survives_reload_and_notify_reaches_callback(tmp_path):
    d = tmp_path / "plugins" / "demo"
    d.mkdir(parents=True)
    (d / "plugin.toml").write_text('name = "demo"\n')
    (d / "plugin.py").write_text(PLUGIN)
    store = ConfigStore(tmp_path)
    store.load()
    heard = []
    host = PluginHost([tmp_path / "plugins"], store, SecretsStore(tmp_path), tmp_path, notify=heard.append)
    host.reload()
    assert await host.call("demo_bump", {}) == "1"
    host.reload()
    assert await host.call("demo_bump", {}) == "2"
    assert await host.call("demo_say", {}) == "ok"
    assert heard == ["hello"]
