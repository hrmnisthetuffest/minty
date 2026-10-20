import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Keep tests away from voice models installed in the real home directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
