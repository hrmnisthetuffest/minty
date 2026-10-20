import pytest

from minty_panel import main as panel_main
from minty_panel.auth import PasswordStore


def test_defaults_and_overrides(tmp_path):
    s = panel_main.load_settings(tmp_path)
    assert (s.host, s.port, s.core_url) == ("127.0.0.1", 7778, "http://127.0.0.1:7777")
    (tmp_path / "panel.toml").write_text('port = 9000\nhost = "0.0.0.0"\ncore_url = "http://127.0.0.1:7800"\n')
    s = panel_main.load_settings(tmp_path)
    assert (s.host, s.port, s.core_url) == ("0.0.0.0", 9000, "http://127.0.0.1:7800")


def test_core_url_follows_configured_core_port(tmp_path):
    (tmp_path / "config.toml").write_text("api_port = 7801\n")
    assert panel_main.load_settings(tmp_path).core_url == "http://127.0.0.1:7801"


@pytest.mark.parametrize("host, expected", [("127.0.0.1", True), ("localhost", True), ("::1", True), ("0.0.0.0", False), ("192.168.1.5", False)])
def test_is_loopback(host, expected):
    assert panel_main.is_loopback(host) is expected


def test_parse_args():
    assert panel_main.parse_args([]).command == "run"
    assert panel_main.parse_args(["set-pin"]).command == "set-pin"
    assert panel_main.parse_args(["set-password"]).command == "set-pin"  # the old name does the same


def test_set_pin_command(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    answers = iter(["493017", "493017"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    panel_main.main(["set-pin"])
    assert PasswordStore(tmp_path).verify("493017")


def test_the_old_command_name_sets_a_pin_too(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    answers = iter(["493017", "493017"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    panel_main.main(["set-password"])
    assert PasswordStore(tmp_path).verify("493017")


def test_set_pin_refuses_an_easy_or_non_numeric_pin(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    for first in ("123456", "correct horse"):
        answers = iter([first, first])
        monkeypatch.setattr("getpass.getpass", lambda prompt="", a=answers: next(a))
        with pytest.raises(SystemExit):
            panel_main.main(["set-pin"])
        assert not PasswordStore(tmp_path).is_set()


def test_set_pin_mismatch_saves_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    answers = iter(["493017", "493018"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    with pytest.raises(SystemExit):
        panel_main.main(["set-pin"])
    assert not PasswordStore(tmp_path).is_set()


def test_main_run_with_malformed_panel_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    (tmp_path / "panel.toml").write_text("invalid toml [[[")
    with pytest.raises(SystemExit) as exc_info:
        panel_main.main(["run"])
    assert "panel.toml" in str(exc_info.value)


def test_build_allowed_hosts_default_is_port_exact():
    s = panel_main.PanelSettings()
    assert panel_main.build_allowed_hosts(s) == ["127.0.0.1:7778", "localhost:7778", "[::1]:7778"]


def test_build_allowed_hosts_non_loopback_adds_host():
    s = panel_main.PanelSettings(host="192.168.1.5", port=9000)
    assert panel_main.build_allowed_hosts(s) == [
        "127.0.0.1:9000", "localhost:9000", "[::1]:9000", "192.168.1.5:9000"]


def _run_main(tmp_path, monkeypatch, panel_toml=None):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    if panel_toml:
        (tmp_path / "panel.toml").write_text(panel_toml)
    captured = {}
    monkeypatch.setattr(panel_main.uvicorn, "run", lambda app, **kw: captured.update(app=app, **kw))
    panel_main.main(["run"])
    return captured


def test_run_disables_proxy_headers_and_passes_allowed_hosts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    captured = _run_main(tmp_path, monkeypatch)
    assert captured["proxy_headers"] is False
    assert (captured["host"], captured["port"]) == ("127.0.0.1", 7778)
    client = TestClient(captured["app"], client=("127.0.0.1", 50000))  # the panel answers only your own network
    assert client.get("/", headers={"host": "localhost:7778"}).status_code == 200
    assert client.get("/", headers={"host": "localhost:1234"}).status_code == 421


def test_run_non_loopback_warns(tmp_path, monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="minty.panel"):
        _run_main(tmp_path, monkeypatch, 'host = "0.0.0.0"\n')
    assert any("0.0.0.0" in r.message for r in caplog.records)


def test_run_loopback_does_not_warn_about_network(tmp_path, monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="minty.panel"):
        _run_main(tmp_path, monkeypatch)
    assert not any("reachable from your network" in r.message for r in caplog.records)


def test_non_integer_port_exits_naming_panel_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    (tmp_path / "panel.toml").write_text('port = "abc"\n')
    with pytest.raises(SystemExit) as exc_info:
        panel_main.main(["run"])
    assert str(exc_info.value) == "Error in panel.toml: port must be a number"


def test_a_short_pin_saves_nothing_and_is_not_echoed(tmp_path, monkeypatch, capsys, caplog):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    answers = iter(["928", "928"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    with caplog.at_level("DEBUG"), pytest.raises(SystemExit) as exc_info:
        panel_main.main(["set-pin"])
    assert exc_info.value.code not in (0, None)
    assert not PasswordStore(tmp_path).is_set()
    out = capsys.readouterr()
    assert "928" not in out.out + out.err + str(exc_info.value) + caplog.text


def test_set_pin_never_echoes_the_pin(tmp_path, monkeypatch, capsys, caplog):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    answers = iter(["493017", "493017"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    with caplog.at_level("DEBUG"):
        panel_main.main(["set-pin"])
    out = capsys.readouterr()
    assert "493017" not in out.out + out.err + caplog.text


def test_the_panel_is_lan_only_unless_you_turn_that_off(tmp_path):
    from minty_panel.main import load_settings

    assert load_settings(tmp_path).lan_only is True
    (tmp_path / "panel.toml").write_text("lan_only = false\n")
    assert load_settings(tmp_path).lan_only is False


def test_extra_host_names_for_the_phone_can_be_listed(tmp_path):
    from minty_panel.main import build_allowed_hosts, load_settings

    (tmp_path / "panel.toml").write_text('host = "192.168.1.20"\nport = 7788\nextra_hosts = ["mypc.local:7788", 5]\n')
    hosts = build_allowed_hosts(load_settings(tmp_path))
    assert "192.168.1.20:7788" in hosts and "mypc.local:7788" in hosts and 5 not in hosts and "127.0.0.1:7788" in hosts
