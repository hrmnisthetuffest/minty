"""A plugin that only works on one system is never loaded, never offered to the model and never claimed as a capability anywhere else (plugins/host.py, mind/registry.py, the built-in manifests)."""
import json
import textwrap
from types import SimpleNamespace as NS

import pytest

from minty_core import osutil
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.mind import registry
from minty_core.plugins.host import BUILTIN_PLUGIN_DIR, PluginHost

PLUGIN = textwrap.dedent('''
    from minty_core.plugins.host import Tool

    def register(ctx):
        async def hello(args):
            return "hello"
        return [Tool("hello", "Says hello.", {"type": "object", "properties": {}}, hello)]
''')


def make(tmp_path, platforms=None, on=None):
    folder = tmp_path / "plugins" / "greeter"
    folder.mkdir(parents=True)
    manifest = 'name = "greeter"\ndescription = "Says hello."\n' + (f"platforms = {json.dumps(platforms)}\n" if platforms is not None else "")
    (folder / "plugin.toml").write_text(manifest, encoding="utf-8")
    (folder / "plugin.py").write_text(PLUGIN, encoding="utf-8")
    store = ConfigStore(tmp_path / "cfg")
    store.load()
    if on is not None:
        data = store.config.model_dump(mode="json")
        data["plugins"] = {"greeter": {"enabled": on}}
        store.update(data)
    host = PluginHost([tmp_path / "plugins"], store, SecretsStore(tmp_path / "cfg"), tmp_path / "cfg")
    host.reload()
    return host


def pretend(monkeypatch, system):
    for flag, name in (("IS_WINDOWS", "windows"), ("IS_LINUX", "linux"), ("IS_MAC", "macos")):
        monkeypatch.setattr(osutil, flag, system == name)


def row(host):
    return next(p for p in host.list_plugins() if p["name"] == "greeter")


def test_a_plugin_for_every_system_loads_everywhere(tmp_path, monkeypatch):
    for system in ("linux", "windows", "macos"):
        pretend(monkeypatch, system)
        host = make(tmp_path / system)
        assert row(host)["tools"] == ["greeter_hello"] and row(host)["unavailable"] == ""


def test_a_linux_only_plugin_loads_on_linux_and_is_absent_on_windows(tmp_path, monkeypatch):
    pretend(monkeypatch, "linux")
    assert row(make(tmp_path / "a", ["linux"]))["tools"] == ["greeter_hello"]
    pretend(monkeypatch, "windows")
    host = make(tmp_path / "b", ["linux"])
    got = row(host)
    assert got["tools"] == [] and got["enabled"] is False and got["error"] is None and got["platforms"] == ["linux"]
    assert got["unavailable"] == "it needs Linux; this is Windows"
    assert host.tool_specs() == []  # the model is never offered a tool that cannot work here


def test_switching_it_on_in_the_settings_does_not_make_it_load_on_the_wrong_system(tmp_path, monkeypatch):
    pretend(monkeypatch, "windows")
    host = make(tmp_path, ["linux"], on=True)
    assert row(host)["enabled"] is False and row(host)["tools"] == [] and host.tool_specs() == []


def test_a_list_of_systems_works_on_each_of_them(tmp_path, monkeypatch):
    for system, loads in (("linux", True), ("windows", True), ("macos", False)):
        pretend(monkeypatch, system)
        assert bool(row(make(tmp_path / system, ["linux", "windows"]))["tools"]) is loads


def test_the_capability_registry_says_not_available_on_this_system_and_why(tmp_path, monkeypatch):
    pretend(monkeypatch, "windows")
    host = make(tmp_path, ["linux"])
    snap = registry.snapshot(host, {}, NS(mcp=None), tmp_path / "cfg", [])
    cap = next(c for c in snap["capabilities"] if c["name"] == "greeter")
    assert cap["status"] == "not available on this system" and "needs Linux" in cap["why"]
    assert registry.find(snap, "say hello")["matches"][0]["status"] == "not available on this system"


def test_the_built_in_plugins_that_only_work_on_linux_are_marked_and_the_cross_platform_ones_are_not():
    from minty_core._toml import tomllib

    marked = {}
    for toml in BUILTIN_PLUGIN_DIR.glob("*/plugin.toml"):
        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        if data.get("platforms"):
            marked[data["name"]] = data["platforms"]
    assert marked == {"system": ["linux"], "shell": ["linux"], "installer": ["linux"], "music_id": ["linux"], "spotify": ["linux"]}


def test_on_windows_none_of_the_linux_desktop_tools_reach_the_model(tmp_path, monkeypatch):
    pretend(monkeypatch, "windows")
    store = ConfigStore(tmp_path)
    store.load()
    host = PluginHost([BUILTIN_PLUGIN_DIR], store, SecretsStore(tmp_path), tmp_path)
    host.reload()
    names = {t.name for t in host.tool_specs()}
    assert not [n for n in names if n.startswith(("system_", "shell_", "installer_", "music_id_", "spotify_"))]
    assert "know_capture" in names and "world_note" in names and "flows_run" in names  # what is cross-platform is all there
    listed = {p["name"]: p for p in host.list_plugins()}
    assert listed["shell"]["unavailable"] and listed["system"]["unavailable"]


def test_the_generated_docs_and_identity_say_which_plugins_are_linux_only():
    from minty_core.dev.plugin_kit import plugin_docs
    from minty_core.mind import identity

    assert "Works on: Linux only." in plugin_docs([BUILTIN_PLUGIN_DIR])
    assert "- `system`:" in identity.static_facts() and "(Linux only)" in identity.static_facts()


@pytest.mark.parametrize("system", ["linux", "windows"])
def test_the_manifest_of_every_built_in_plugin_names_only_known_systems(system):
    from minty_core._toml import tomllib

    for toml in BUILTIN_PLUGIN_DIR.glob("*/plugin.toml"):
        for name in tomllib.loads(toml.read_text(encoding="utf-8")).get("platforms", []):
            assert name in osutil.PLATFORM_NAMES, (toml, name)
