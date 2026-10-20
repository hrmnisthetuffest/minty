import os
import stat

import pytest
from pydantic import ValidationError

from minty_core.config import ConfigError, ConfigStore, ProviderConfig
from minty_core.keystore import SecretsStore, mask_key


def test_defaults_roundtrip(tmp_path):
    store = ConfigStore(tmp_path)
    cfg = store.load()
    assert cfg.personality.active == "minty"
    assert [p.name for p in cfg.providers][:2] == ["groq", "cerebras"]
    store.update(cfg.model_dump(mode="json"))
    assert ConfigStore(tmp_path).load() == cfg


def test_invalid_update_keeps_previous(tmp_path):
    store = ConfigStore(tmp_path)
    cfg = store.load()
    store.update(cfg.model_dump(mode="json"))
    before = store.path.read_text()
    bad = cfg.model_dump(mode="json")
    bad["personality"]["humor"] = 99
    with pytest.raises(ValidationError):
        store.update(bad)
    assert store.path.read_text() == before
    assert store.config.personality.humor == cfg.personality.humor


def test_subscribers_notified(tmp_path):
    store = ConfigStore(tmp_path)
    cfg = store.load()
    seen = []
    store.subscribe(seen.append)
    data = cfg.model_dump(mode="json")
    data["personality"]["humor"] = 7
    store.update(data)
    assert seen[0].personality.humor == 7


def test_secrets_roundtrip_mode_and_mask(tmp_path):
    s = SecretsStore(tmp_path)
    s.set("groq", "gsk_abcdefghijklmnop")
    assert s.get("groq") == "gsk_abcdefghijklmnop"
    assert stat.S_IMODE(os.stat(s.path).st_mode) == 0o600
    assert s.masked() == {"groq": "gsk…mnop"}
    assert s.delete("groq") is True
    assert s.get("groq") is None
    assert s.delete("groq") is False


def test_secret_names_with_dots(tmp_path):
    SecretsStore(tmp_path).set("plugin.home.token", "tok")
    assert SecretsStore(tmp_path).get("plugin.home.token") == "tok"


def test_mask_short_key_hides_everything():
    assert mask_key("short") == "…"


def test_listener_exception_isolation(tmp_path):
    store = ConfigStore(tmp_path)
    cfg = store.load()
    seen = []

    def bad_listener(config):
        raise RuntimeError("listener failure")

    def good_listener(config):
        seen.append(config)

    store.subscribe(bad_listener)
    store.subscribe(good_listener)

    data = cfg.model_dump(mode="json")
    data["personality"]["humor"] = 8
    result = store.update(data)

    # update() returns normally despite listener exception
    assert result.personality.humor == 8
    # second listener ran
    assert len(seen) == 1
    assert seen[0].personality.humor == 8
    # store.config is the new config
    assert store.config.personality.humor == 8


def test_load_malformed_toml(tmp_path):
    store = ConfigStore(tmp_path)
    store.path.write_text("this is = = not toml")

    with pytest.raises(ConfigError) as exc_info:
        store.load()
    assert str(store.path) in str(exc_info.value)


def test_load_schema_invalid(tmp_path):
    store = ConfigStore(tmp_path)
    store.path.write_text("[personality]\nhumor = 99\n")

    with pytest.raises(ConfigError) as exc_info:
        store.load()
    assert str(store.path) in str(exc_info.value)


def test_nvidia_provider_in_defaults(tmp_path):
    store = ConfigStore(tmp_path)
    cfg = store.load()
    nvidia = next((p for p in cfg.providers if p.name == "nvidia"), None)
    assert nvidia is not None
    assert nvidia.base_url == "https://integrate.api.nvidia.com/v1"


def test_default_deep_provider_shares_the_nvidia_key(tmp_path):
    providers = {p.name: p for p in ConfigStore(tmp_path).load().providers}
    deep = providers["nvidia-deep"]
    assert (deep.tier, deep.key_name, deep.supports_tools) == ("deep", "nvidia", True)
    assert deep.model == "moonshotai/kimi-k3" and deep.extra == {"reasoning_effort": "max"}
    assert providers["nvidia-gemma"].key_name == "nvidia"
    assert providers["mistral"].allow_sensitive is False
    assert providers["groq"].tier == "fast" and providers["groq"].key_name == ""


def test_provider_timeout_defaults_and_bounds(tmp_path):
    providers = {p.name: p for p in ConfigStore(tmp_path).load().providers}
    assert providers["groq"].timeout_s == 30.0 and providers["nvidia-deep"].timeout_s == 180.0
    for bad in (0, -1, 901):
        with pytest.raises(ValidationError):
            ProviderConfig(name="x", model="m", timeout_s=bad)


@pytest.mark.parametrize("extra", [
    {"stop": [None, "x"]},
    {"nested": {"a": {"b": None}}},
    {"api_key": "x"},
    {"Authorization": "Bearer x"},
    {"auth": {"access_token": "x"}},
    {"client_secret": "x"},
    {"password": "x"},
    {"bad": object()},
    {"bad": float("nan")},
])
def test_extra_must_be_json_without_none_or_credentials(extra):
    with pytest.raises(ValidationError):
        ProviderConfig(name="x", model="m", extra=extra)


def test_extra_accepts_normal_sampling_fields():
    extra = {"reasoning_effort": "max", "max_tokens": 512, "stop": ["a"], "chat_template_kwargs": {"thinking": True}}
    assert ProviderConfig(name="x", model="m", extra=extra).extra == extra
