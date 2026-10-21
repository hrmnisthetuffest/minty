"""Everything that can be set has a place in the panel's settings, and every place points at a real setting."""
import re
from pathlib import Path

from minty_core.config import Config
from minty_core.flags import LABELS, list_flags

STATIC = Path(__file__).parent.parent / "src" / "minty_panel" / "static"


def registry() -> list[tuple[str, str, str]]:
    """(section, key, kind) of every row in static/settings-schema.js."""
    text = (STATIC / "settings-schema.js").read_text()
    label = r'"(?:[^"\\]|\\.)*"'
    return re.findall(r'\[\s*"([a-z_]*)",\s*"([a-z_0-9]+)",\s*' + label + r',\s*"([a-z]+(?::[^"]*)?)"', text)


def resolve(config: Config, section: str, key: str):
    node = getattr(config, section) if section else config
    assert key in type(node).model_fields, f"{section}.{key} is not a setting"
    return getattr(node, key)


def test_every_row_in_the_panel_is_a_real_setting_of_the_right_kind():
    config = Config()
    rows = registry()
    assert len(rows) > 50
    for section, key, kind in rows:
        value = resolve(config, section, key)
        if kind == "bool":
            assert isinstance(value, bool), (section, key)
        elif kind.startswith("number"):
            assert isinstance(value, (int, float)) and not isinstance(value, bool), (section, key)
        elif kind.startswith("select:"):
            assert str(value) in kind[7:].split(","), (section, key, value)


def test_the_settings_added_in_the_recent_versions_can_all_be_set_in_the_panel():
    have = {(s, k) for s, k, _ in registry()}
    for wanted in [
        ("audio", "auto_sensitivity"), ("audio", "mic_sensitivity"), ("audio", "speech_rms_threshold"), ("audio", "listen_duck"),
        ("audio", "listen_duck_level"), ("audio", "music_aware"), ("audio", "music_pause"), ("audio", "speculative_stt"),
        ("audio", "spec_after_ms"), ("audio", "early_finish_ms"), ("turn", "first_clause_words"), ("router", "fast_reasoning"),
        ("instant", "enabled"), ("instant", "actions"), ("update", "repo"), ("update", "check"), ("update", "check_every_hours"),
        ("update", "auto_install"), ("update", "verify_signed"), ("", "terminal_hotkeys"), ("", "terminal_mouse"),
        ("", "terminal_mute_key"), ("", "terminal_listen_key"), ("", "terminal_reduced_motion"), ("", "terminal_images"),
        ("", "terminal_cover_size"), ("", "clock_24h"), ("profile", "enabled"), ("profile", "max_chars"),
        ("router", "warm_on_wake"), ("turn", "stable_prefix"), ("turn", "lean_prompt"), ("tts", "parallel"), ("stt", "auto_model"),
        ("audio", "live_barge_in"), ("audio", "live_barge_in_rms"),
    ]:
        assert wanted in have, f"{wanted} has no place in the panel's settings"


def test_the_browser_plugin_offers_the_google_switch():
    from minty_core._toml import tomllib
    manifest = Path(__file__).parent.parent / "src" / "minty_core" / "plugins" / "builtin" / "browser" / "plugin.toml"
    spec = tomllib.loads(manifest.read_text())["settings"]["avoid_google"]
    assert spec["type"] == "boolean" and spec["default"] is True


def test_the_new_switches_have_plain_labels():
    for path in ("clock_24h", "audio.auto_sensitivity", "audio.listen_duck", "terminal_hotkeys", "terminal_mouse",
                 "audio.speculative_stt", "instant.enabled", "instant.actions"):
        assert path in LABELS, path
    assert {f["path"] for f in list_flags(Config())} >= {"clock_24h", "audio.listen_duck"}
