from minty_core.paths import config_dir


def test_config_lives_in_minty(monkeypatch, tmp_path):
    monkeypatch.delenv("MINTY_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_dir() == tmp_path / "minty"


def test_a_folder_can_be_chosen_by_hand(monkeypatch, tmp_path):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "mine"))
    assert config_dir() == tmp_path / "mine"
