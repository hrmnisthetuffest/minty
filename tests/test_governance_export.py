"""615: an encrypted export of everything, split for Telegram, imported into a new folder."""
import io
import os
import sqlite3
import tarfile

import pytest

from minty_core.governance import export as X
from minty_core.vault.store import Vault

PASS = "a long enough passphrase"


@pytest.fixture
def world(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.toml").write_text('[memory]\nwindow_min = 30\n')
    (cfg / "memory.md").write_text("- Prefers metric units SECRET-MARKER-ONE\n")
    (cfg / "secrets.toml").write_text("groq = 'gsk_should_never_travel'\n")
    (cfg / "api_token").write_text("TOKEN-NEVER")
    (cfg / "standing.json").write_text('{"grants": []}')
    (cfg / "history" / "raw").mkdir(parents=True)
    (cfg / "history" / "raw" / "2026-10-12.jsonl").write_text('{"text": "raw talk SECRET-MARKER-TWO"}\n')
    for name in ("memory.db", "butler.db"):
        db = sqlite3.connect(cfg / name)
        db.execute("CREATE TABLE t (v TEXT)")
        db.execute("INSERT INTO t VALUES (?)", (f"row in {name}",))
        db.commit()
        db.close()
    vault = Vault(tmp_path / "brain", git=True)
    vault.create("Camera wiring", "ribbon cable SECRET-MARKER-THREE", folder="notes")
    return {"cfg": cfg, "vault": vault, "tmp": tmp_path}


def make(world, out=None):
    out = out or world["tmp"] / "out" / "export.hdx"
    return X.export_everything(world["cfg"], world["vault"].root, PASS, out), out


def test_the_export_holds_no_plaintext_and_the_right_things(world):
    info, out = make(world)
    blob = out.read_bytes()
    for marker in (b"SECRET-MARKER-ONE", b"SECRET-MARKER-TWO", b"SECRET-MARKER-THREE", b"Camera wiring"):
        assert marker not in blob
    assert info.files >= 6 and oct(os.stat(out).st_mode & 0o777) == "0o600"


def test_import_goes_into_a_new_folder_and_matches(world):
    _, out = make(world)
    target = world["tmp"] / "restored"
    n = X.import_everything(out, PASS, target)
    assert n >= 6
    assert "SECRET-MARKER-ONE" in (target / "config" / "memory.md").read_text()
    assert "SECRET-MARKER-TWO" in (target / "config" / "history" / "raw" / "2026-10-12.jsonl").read_text()
    assert sqlite3.connect(target / "config" / "butler.db").execute("SELECT v FROM t").fetchone() == ("row in butler.db",)
    notes = list((target / "vault" / "notes").glob("*.md"))
    assert len(notes) == 1 and "SECRET-MARKER-THREE" in notes[0].read_text()
    assert (target / "vault" / ".git").is_dir()


def test_keys_tokens_approvals_and_the_rebuildable_index_never_travel(world):
    _, out = make(world)
    target = world["tmp"] / "restored"
    X.import_everything(out, PASS, target)
    assert not (target / "config" / "secrets.toml").exists()
    assert not (target / "config" / "api_token").exists()
    assert not (target / "config" / "standing.json").exists()
    assert not (target / "vault" / "_minty" / "index.db").exists()


def test_wrong_passphrase_or_a_changed_file_is_refused(world):
    _, out = make(world)
    with pytest.raises(X.ExportError):
        X.import_everything(out, "not the passphrase", world["tmp"] / "r1")
    blob = bytearray(out.read_bytes())
    blob[-5] ^= 0xFF
    out.write_bytes(bytes(blob))
    with pytest.raises(X.ExportError):
        X.import_everything(out, PASS, world["tmp"] / "r2")
    assert not (world["tmp"] / "r2").exists() or not any((world["tmp"] / "r2").iterdir())


def test_import_never_writes_into_a_folder_that_has_things_in_it(world):
    _, out = make(world)
    busy = world["tmp"] / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("mine")
    with pytest.raises(X.ExportError, match="not empty"):
        X.import_everything(out, PASS, busy)
    assert (busy / "keep.txt").read_text() == "mine"


def test_a_hostile_archive_cannot_write_outside_the_target(tmp_path):
    from minty_core.backup import _crypto, _key

    AESGCM, _s, _b = _crypto()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for name in ("../escape.txt", "/abs.txt", "config/ok.txt"):
            info = tarfile.TarInfo(name)
            info.size = 2
            tar.addfile(info, io.BytesIO(b"hi"))
        manifest = b'{"files": {}}'
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    salt, nonce = os.urandom(16), os.urandom(12)
    blob = X.MAGIC + salt + nonce + AESGCM(_key(PASS.encode(), salt)).encrypt(nonce, raw.getvalue(), X.MAGIC)
    f = tmp_path / "evil.hdx"
    f.write_bytes(blob)
    target = tmp_path / "t"
    X.import_everything(f, PASS, target)
    assert (target / "config" / "ok.txt").exists()
    assert not (tmp_path / "escape.txt").exists() and not (target / "abs.txt").exists()


def test_a_short_passphrase_is_refused(world):
    with pytest.raises(X.ExportError):
        X.export_everything(world["cfg"], world["vault"].root, "short", world["tmp"] / "x.hdx")


def test_parts_for_telegram_split_and_join_exactly(world):
    _, out = make(world)
    parts = X.split_file(out, max_bytes=2048)
    assert len(parts) >= 2 and all(p.stat().st_size <= 2048 for p in parts)
    assert [p.name for p in parts] == sorted(p.name for p in parts)
    joined = X.join_parts(parts[0], world["tmp"] / "joined.hdx")
    assert joined.read_bytes() == out.read_bytes()
    assert X.import_everything(joined, PASS, world["tmp"] / "from-parts") >= 6


def test_the_default_part_size_fits_a_telegram_file():
    assert X.PART_BYTES <= 49 * 1024 * 1024


def test_verify_reports_a_good_export_and_a_bad_one(world):
    _, out = make(world)
    assert X.verify(out, PASS).ok
    assert not X.verify(out, "wrong passphrase!").ok


def test_the_terminal_commands_export_verify_and_import(world, capsys):
    answers = iter([PASS, PASS])
    out = world["tmp"] / "ws" / "exports" / "e.hdx"
    code = X.cli(["export", "--out", str(out)], world["cfg"], world["vault"].root, world["tmp"] / "ws",
                 ask_secret=lambda prompt: next(answers))
    text = capsys.readouterr().out
    assert code == 0 and out.exists() and "keys and the API token are not included" in text
    assert PASS not in text
    assert X.cli(["verify", str(out)], world["cfg"], None, world["tmp"], ask_secret=lambda p: PASS) == 0
    assert "looks good" in capsys.readouterr().out
    target = world["tmp"] / "back"
    assert X.cli(["import", str(out), str(target)], world["cfg"], None, world["tmp"], ask_secret=lambda p: PASS) == 0
    assert (target / "config" / "memory.md").exists()
    assert X.cli(["import", str(out), str(target)], world["cfg"], None, world["tmp"], ask_secret=lambda p: PASS) == 1
    assert "not empty" in capsys.readouterr().out


def test_export_mismatched_passphrases_make_nothing(world, capsys):
    answers = iter([PASS, "something different"])
    out = world["tmp"] / "no.hdx"
    code = X.cli(["export", "--out", str(out)], world["cfg"], world["vault"].root, world["tmp"],
                 ask_secret=lambda p: next(answers))
    assert code == 1 and not out.exists() and "did not match" in capsys.readouterr().out


def test_export_splits_when_asked(world, capsys, monkeypatch):
    monkeypatch.setattr(X, "PART_BYTES", 4096)
    answers = iter([PASS, PASS])
    out = world["tmp"] / "s.hdx"
    code = X.cli(["export", "--out", str(out), "--split"], world["cfg"], world["vault"].root, world["tmp"],
                 ask_secret=lambda p: next(answers))
    assert code == 0 and (world["tmp"] / "s.hdx.part001").exists() and "parts" in capsys.readouterr().out


def test_verify_or_import_of_a_missing_file_is_a_plain_message_not_a_traceback(tmp_path, capsys):
    from minty_core.governance.export import cli

    secret = lambda prompt: "a long enough passphrase"  # noqa: E731
    for argv in (["verify", str(tmp_path / "nope.enc")], ["import", str(tmp_path / "nope.enc"), str(tmp_path / "out")]):
        assert cli(argv, tmp_path, None, tmp_path, ask_secret=secret) == 1
        out = capsys.readouterr().out
        assert "nope.enc" in out and "Traceback" not in out
