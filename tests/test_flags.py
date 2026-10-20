"""The feature-flag page: every switch listed, each one settable (W1-28)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeAgent
from minty_core.api import create_app
from minty_core.config import Config, ConfigStore
from minty_core.context import build_context
from minty_core.flags import list_flags, set_flag

STATIC = Path(__file__).resolve().parents[1] / "src" / "minty_panel" / "static"


def test_the_switches_are_listed_with_labels_and_no_secrets():
    flags = {f["path"]: f for f in list_flags(Config())}
    assert flags["safety.privacy_mode"]["value"] is False and "don't train" in flags["safety.privacy_mode"]["label"]
    assert flags["memory.auto_remember"]["value"] is True and flags["toolkit_routing"]["group"] == "general"
    assert flags["jobs.resume"]["group"] == "jobs"
    assert not any(p.startswith(("providers", "plugins", "debug")) for p in flags)
    assert all(isinstance(f["value"], bool) and f["label"] for f in flags.values())
    assert not any("key" in p.lower() and "secret" in f["label"].lower() for p, f in flags.items())


def test_setting_one_saves_it_and_rejects_what_is_not_a_switch(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    assert set_flag(store, "safety.privacy_mode", True) is True and store.config.safety.privacy_mode is True
    assert ConfigStore(tmp_path).load().safety.privacy_mode is True  # on disk
    assert set_flag(store, "safety.mode", True) is False  # a choice, not a switch
    assert set_flag(store, "nonsense.thing", True) is False
    with pytest.raises(ValueError):
        set_flag(store, "safety.privacy_mode", "yes")


def test_the_api_lists_and_sets_with_the_token(tmp_path):
    ctx = build_context(tmp_path)
    ctx.agent = FakeAgent([["ok"]])
    client = TestClient(create_app(ctx))
    auth = {"Authorization": f"Bearer {ctx.token}"}
    assert client.get("/flags").status_code == 401
    listed = client.get("/flags", headers=auth).json()
    assert any(f["path"] == "offline.enabled" and f["value"] is True for f in listed)
    out = client.post("/flags", json={"path": "offline.enabled", "value": False}, headers=auth)
    assert out.status_code == 200 and out.json() == {"path": "offline.enabled", "value": False}
    assert ctx.store.config.offline.enabled is False
    assert client.post("/flags", json={"path": "nope.nope", "value": True}, headers=auth).status_code == 404
    assert client.post("/flags", json={"path": "offline.enabled", "value": "x"}, headers=auth).status_code == 422


def test_the_panel_has_the_page_and_the_menu_entry():
    assert (STATIC / "pages" / "flags.js").exists()
    app_js = (STATIC / "app.js").read_text()
    assert 'import flags from "./pages/flags.js"' in app_js and '"flags"' in app_js
