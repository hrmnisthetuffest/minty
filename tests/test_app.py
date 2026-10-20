import os
import stat

import pytest

from minty_core import app
from minty_core.config import AudioConfig
from minty_core.keystore import SecretsStore


def test_parse_args_defaults_to_run():
    assert app.parse_args([]).command == "run"
    args = app.parse_args(["set-key", "groq"])
    assert (args.command, args.name) == ("set-key", "groq")


def test_diff_audio():
    old = AudioConfig()
    assert app.diff_audio(old, old) == set()
    new = AudioConfig(wake_word="alexa", input_device="2")
    assert app.diff_audio(old, new) == {"wake_word", "input_device"}


def test_set_key_command_stores_secret_without_echo(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": " gsk_abcdefghijklmnop ")
    app.main(["set-key", "groq"])
    store = SecretsStore(tmp_path)
    assert store.get("groq") == "gsk_abcdefghijklmnop"
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert "gsk_abcdefghijklmnop" not in capsys.readouterr().out


def test_run_exits_cleanly_on_malformed_config(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text("not = = toml")
    with pytest.raises(SystemExit) as exc:
        app.main(["run"])
    assert "config.toml" in str(exc.value)


def test_log_task_failure_logs_error_and_ignores_cancelled(caplog):
    import asyncio
    import logging

    async def scenario():
        async def boom():
            raise RuntimeError("mic gone")

        failed = asyncio.create_task(boom())
        await asyncio.gather(failed, return_exceptions=True)
        with caplog.at_level(logging.ERROR, logger="minty"):
            app._log_task_failure(failed, "restart")
        assert any("mic gone" in (r.exc_text or "") or r.exc_info and "mic gone" in str(r.exc_info[1]) for r in caplog.records)

        caplog.clear()
        slow = asyncio.create_task(asyncio.sleep(10))
        slow.cancel()
        await asyncio.gather(slow, return_exceptions=True)
        with caplog.at_level(logging.ERROR, logger="minty"):
            app._log_task_failure(slow, "restart")
        assert caplog.records == []

    asyncio.run(scenario())


def test_parse_args_import_keys():
    args = app.parse_args(["import-keys", "some/file"])
    assert (args.command, args.path) == ("import-keys", "some/file")


def _key_file(tmp_path):
    groq = "gsk_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    nvidia = "nvapi-" + "FAKEfakeFAKEfake_0123456789-abcdefghijklmn"
    mistral = "mistralFAKE0123456789abcdefghijklmnop"
    openrouter = "sk-or-v1-" + "0123456789abcdef0123456789abcdef"
    path = tmp_path / "my keys.txt"
    path.write_text(
        f"groq {groq}\n"
        f"Mistral: {mistral}\n"
        f"open router {openrouter}\n"
        f'headers = {{"Authorization": "Bearer {nvidia}"}}\n',
        encoding="utf-8",
    )
    return path, {"groq": groq, "mistral": mistral, "openrouter": openrouter, "nvidia": nvidia}


def test_import_keys_stores_secrets_and_never_prints_values(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config"
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(config))
    path, expected = _key_file(tmp_path)
    app.main(["import-keys", str(path)])
    out = capsys.readouterr()
    assert "Imported 4 keys: groq, mistral, nvidia, openrouter" in out.out
    assert f"Now move or delete {path} — it holds your keys in plain text." in out.out
    for value in expected.values():
        assert value not in out.out and value not in out.err
        assert value[8:] not in out.out and value[8:] not in out.err
    store = SecretsStore(config)
    for name, value in expected.items():
        assert store.get(name) == value
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


def test_import_keys_stores_brave_under_plugin_secret_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "keys.txt"
    path.write_text("brave BSAfakefakefake0123456789ABCDEF\n", encoding="utf-8")
    app.main(["import-keys", str(path)])
    out = capsys.readouterr().out
    assert "Imported 1 key: plugin.web_search.brave_key" in out
    assert "BSAfakefakefake" not in out
    assert SecretsStore(tmp_path / "config").get("plugin.web_search.brave_key") == "BSAfakefakefake0123456789ABCDEF"


def test_import_keys_unreadable_path_exits_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "config"))
    with pytest.raises(SystemExit) as exc:
        app.main(["import-keys", str(tmp_path / "missing.txt")])
    assert "missing.txt" in str(exc.value)
    assert not (tmp_path / "config" / "secrets.toml").exists()
    with pytest.raises(SystemExit):
        app.main(["import-keys", str(tmp_path)])


def test_import_keys_no_keys_found_exits_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "notes.txt"
    path.write_text("nothing to see here\ngroq: short\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        app.main(["import-keys", str(path)])
    assert "no API keys" in str(exc.value)
    assert not (tmp_path / "config" / "secrets.toml").exists()


def test_import_keys_help_says_existing_keys_are_replaced(capsys):
    with pytest.raises(SystemExit):
        app.parse_args(["import-keys", "--help"])
    assert "existing keys with the same name are replaced" in " ".join(capsys.readouterr().out.split())


def test_import_keys_lists_replaced_names_but_never_values(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config"
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(config))
    store = SecretsStore(config)
    store.set("groq", "old-groq-value-123456")
    store.set("untouched", "stays-the-same-12345")
    os.chmod(store.path, 0o644)
    path, expected = _key_file(tmp_path)
    app.main(["import-keys", str(path)])
    out = capsys.readouterr().out
    assert "Imported 4 keys: groq, mistral, nvidia, openrouter" in out
    assert "Replaced existing: groq\n" in out
    for value in [*expected.values(), "old-groq-value-123456", "stays-the-same-12345"]:
        assert value not in out
    assert store.get("groq") == expected["groq"]
    assert store.get("untouched") == "stays-the-same-12345"
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


def test_import_keys_without_existing_keys_prints_no_replaced_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "config"))
    path, _ = _key_file(tmp_path)
    app.main(["import-keys", str(path)])
    assert "Replaced" not in capsys.readouterr().out


def test_import_keys_rejects_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "config"))
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * (2 << 20))
    with pytest.raises(SystemExit) as exc:
        app.main(["import-keys", str(big)])
    assert "file too large for a keys file" in str(exc.value)
    assert not (tmp_path / "config" / "secrets.toml").exists()


def test_import_keys_accepts_file_at_the_size_limit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "config"))
    line = b"groq gsk_" + b"A" * 30 + b"\n"
    edge = tmp_path / "edge.txt"
    edge.write_bytes(line + b"#" * ((1 << 20) - len(line)))
    app.main(["import-keys", str(edge)])
    assert "Imported 1 key: groq" in capsys.readouterr().out


def test_import_keys_rejects_non_regular_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "config"))
    with pytest.raises(SystemExit) as exc:
        app.main(["import-keys", str(tmp_path)])
    assert "not a regular file" in str(exc.value)
    if not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo")
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(SystemExit) as exc:
        app.main(["import-keys", str(fifo)])
    assert "not a regular file" in str(exc.value)


def test_import_keys_corrupt_secrets_file_exits_cleanly(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config"
    config.mkdir()
    (config / "secrets.toml").write_text("this = = broken gsk_secretfragment0123456789", encoding="utf-8")
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(config))
    path, _ = _key_file(tmp_path)
    with pytest.raises(SystemExit) as exc:
        app.main(["import-keys", str(path)])
    assert str(exc.value) == "secrets.toml is not valid TOML; fix or delete it"
    assert "gsk_secretfragment" not in capsys.readouterr().out
    assert "broken" in (config / "secrets.toml").read_text(encoding="utf-8")


def test_import_keys_undecodable_bytes_do_not_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "latin.txt"
    path.write_bytes(b"caf\xe9\ngroq gsk_" + b"B" * 30 + b"\n")
    app.main(["import-keys", str(path)])
    assert "Imported 1 key: groq" in capsys.readouterr().out


def test_main_quiets_http_client_loggers_that_log_request_urls(tmp_path, monkeypatch):
    import logging

    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "gsk_abcdefghijklmnop")
    names = ("httpx", "httpcore")
    saved = {n: logging.getLogger(n).level for n in names}
    try:
        for n in names:
            logging.getLogger(n).setLevel(logging.NOTSET)
        app.main(["set-key", "groq"])
        for n in names:
            assert logging.getLogger(n).level == logging.WARNING
    finally:
        for n, level in saved.items():
            logging.getLogger(n).setLevel(level)


def test_core_cli_accepts_no_panel_and_set_password():
    from minty_core.app import parse_args

    assert parse_args([]).no_panel is False
    assert parse_args(["--no-panel"]).no_panel is True
    assert parse_args(["run", "--no-panel"]).no_panel is True
    assert parse_args(["set-password"]).command == "set-password"


async def test_combined_run_starts_panel_and_core_in_one_process(tmp_path, monkeypatch):
    import asyncio

    import httpx

    import minty_core.app as app

    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "build_voice_pipeline", lambda ctx: (_ for _ in ()).throw(RuntimeError("no audio")))
    ports = {}
    from minty_core.config import ConfigStore

    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["api_port"] = 17977
    store.update(data)
    (tmp_path / "panel.toml").write_text("port = 17978\n")
    task = asyncio.create_task(app._run(tmp_path))
    try:
        async with httpx.AsyncClient() as c:
            for _ in range(100):
                try:
                    r = await c.get("http://127.0.0.1:17978/")
                    if r.status_code in (200, 302, 401):
                        break
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
            assert r.status_code in (200, 302, 401)
            core = await c.get("http://127.0.0.1:17977/status", headers={"Authorization": "Bearer nope"})
            assert core.status_code in (401, 403)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_request_restart_stops_the_server_and_main_reexecs(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import minty_core.app as app

    server = SimpleNamespace(should_exit=False)
    app._restart.update(requested=False, server=server)
    app.request_restart()
    assert app._restart["requested"] is True and server.should_exit is True

    calls = []

    async def fake_run(directory, with_panel=True, safe=False):
        app.request_restart()

    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "_run", fake_run)
    monkeypatch.setattr(app, "_reexec", lambda: calls.append("exec"))
    app.main([])
    assert calls == ["exec"]
    async def quiet_run(directory, with_panel=True, safe=False):
        return None

    monkeypatch.setattr(app, "_run", quiet_run)
    app._restart.update(requested=False, server=None)
    app.main([])  # a run that ends normally does not restart
    assert calls == ["exec"]


def test_restart_app_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from minty_core.api import create_app
    from minty_core.context import build_context

    ctx = build_context(tmp_path)
    c = TestClient(create_app(ctx), headers={"Authorization": f"Bearer {ctx.token}"})
    assert c.post("/restart-app").status_code == 503
    hits = []
    ctx.services["restart"] = lambda: hits.append(1)
    r = c.post("/restart-app")
    assert r.status_code == 202 and r.json() == {"restarting": True}


def test_jobs_interrupted_by_a_restart_are_announced_once(tmp_path):
    from minty_core.app import announce_interrupted
    from minty_core.context import build_context
    from minty_core.jobs import Job, JobStore
    JobStore(tmp_path / "jobs" / "jobs.jsonl").save(Job(id=2, kind="browser", goal="find a lamp", status="running"))
    ctx = build_context(tmp_path)
    q = ctx.bus.subscribe({"notify"})
    assert announce_interrupted(ctx) == "My earlier browser job 2 to find a lamp was interrupted when I restarted."
    assert q.get_nowait()["text"].startswith("My earlier browser job 2")
    assert announce_interrupted(build_context(tmp_path)) == ""  # not again on the next start


def test_commands_with_their_own_options_keep_them():
    from minty_core.app import parse_args

    assert parse_args(["tune", "--lite", "--apply"]).rest == ["--lite", "--apply"]
    assert parse_args(["update", "--check"]).rest == ["--check"] and parse_args(["update", "--yes"]).command == "update"
    assert parse_args(["instant", "what", "is", "12", "times", "8"]).rest == ["what", "is", "12", "times", "8"]
    assert parse_args(["instant"]).rest == [] and parse_args([]).command == "run"
