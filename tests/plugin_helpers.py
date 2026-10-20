import httpx

from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.plugins.host import BUILTIN_PLUGIN_DIR, PluginHost


def make_host(tmp_path, notify=None, plugins=None, secrets=None, services=None):
    """Host with only the built-in plugins. `plugins` maps plugin name -> config entry."""
    store = ConfigStore(tmp_path)
    store.load()
    if plugins:
        data = store.config.model_dump(mode="json")
        data["plugins"] = plugins
        store.update(data)
    keys = SecretsStore(tmp_path)
    for name, value in (secrets or {}).items():
        keys.set(name, value)
    host = PluginHost([BUILTIN_PLUGIN_DIR], store, keys, tmp_path, notify=notify, services=services)
    host.reload()
    return host


def mock_http(monkeypatch, handler):
    """Route minty_core.httpclient.get_client() to a MockTransport client."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("minty_core.httpclient._client", client)
    return client
