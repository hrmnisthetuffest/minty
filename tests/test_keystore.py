import os
import stat

from minty_core.keystore import SecretsStore


def test_set_many_writes_all_values_atomically_with_mode_0600(tmp_path):
    store = SecretsStore(tmp_path)
    store.set("keep", "k1")
    store.set_many({"a": "va", "plugin.x.b": "vb", "keep": "k2"})
    assert SecretsStore(tmp_path).names() == ["a", "keep", "plugin.x.b"]
    assert (store.get("a"), store.get("plugin.x.b"), store.get("keep")) == ("va", "vb", "k2")
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


def test_set_many_tightens_a_loose_existing_file(tmp_path):
    store = SecretsStore(tmp_path)
    store.set("old", "v")
    os.chmod(store.path, 0o644)
    store.set_many({"new": "w"})
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert store.get("old") == "v"


def test_set_many_writes_once(tmp_path, monkeypatch):
    store = SecretsStore(tmp_path)
    writes = []
    monkeypatch.setattr(store, "_write", lambda data: writes.append(dict(data)))
    store.set_many({"a": "1", "b": "2"})
    assert writes == [{"a": "1", "b": "2"}]
