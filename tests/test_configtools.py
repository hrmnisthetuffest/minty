"""Config snapshots, a readable diff and plain validation errors (W1B-02)."""
import pytest

from minty_core.config import Config, ConfigError, ConfigStore
from minty_core.configtools import ConfigSnapshots, diff_dicts, explain_validation


def test_diff_names_each_change_in_words():
    old = {"safety": {"mode": "commits", "privacy_mode": False}, "api_port": 7777, "audio": {"wake_phrases": ["a"]}}
    new = {"safety": {"mode": "careful", "privacy_mode": True}, "api_port": 7777, "extra": 1,
           "audio": {"wake_phrases": ["a", "b"]}}
    lines = [c.words() for c in diff_dicts(old, new)]
    assert lines == ["audio.wake_phrases: ['a'] -> ['a', 'b']", "extra was added: 1",
                     "safety.mode: 'commits' -> 'careful'", "safety.privacy_mode: off -> on"]


def test_snapshots_skip_duplicates_keep_the_newest_and_show_the_last_change(tmp_path):
    clock = {"t": 1_700_000_000.0}
    s = ConfigSnapshots(tmp_path / "snaps", keep=3, clock=lambda: clock["t"])
    assert s.take({"a": 1}) is not None and s.take({"a": 1}) is None
    for n in (2, 3, 4):
        clock["t"] += 10
        s.take({"a": n})
    assert len(s.list()) == 3  # only the newest three
    assert [c.words() for c in s.last_change()] == ["a: 3 -> 4"]


def test_validation_errors_are_plain():
    with pytest.raises(Exception) as e:
        Config.model_validate({"safety": {"mode": "carefull", "default_hourly_limit_changes": "many"},
                               "api_port": 80, "turn": {"keep_full_turns": 99}, "nonsense": 1})
    lines = explain_validation(e.value)
    text = "\n".join(lines)
    assert "safety.mode: must be one of 'careful', 'commits' or 'trusted' (you have 'carefull')" in text
    assert "safety.default_hourly_limit_changes: must be a whole number (you have 'many')" in text
    assert "api_port: must be at least 1024 (you have 80)" in text
    assert "turn.keep_full_turns: must be at most 50 (you have 99)" in text


def test_a_broken_config_file_is_explained_when_loaded(tmp_path):
    (tmp_path / "config.toml").write_text('[safety]\nmode = "carefull"\n')
    with pytest.raises(ConfigError) as e:
        ConfigStore(tmp_path).load()
    assert "safety.mode: must be one of" in str(e.value) and "config.toml" in str(e.value)


def test_the_store_takes_a_snapshot_on_every_change(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    snaps = ConfigSnapshots(tmp_path / "config-snapshots")
    store.subscribe(lambda cfg: snaps.take(cfg.model_dump(mode="json", exclude_none=True)))
    data = store.config.model_dump(mode="json")
    data["safety"]["privacy_mode"] = True
    store.update(data)
    data["safety"]["privacy_mode"] = False
    store.update(data)
    assert [c.words() for c in snaps.last_change()] == ["safety.privacy_mode: on -> off"]
