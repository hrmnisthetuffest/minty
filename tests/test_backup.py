"""Encrypted backups and the restore test (W1B-03)."""
import sqlite3

import pytest

from minty_core.backup import BackupError, create, load_or_make_keyfile, prune, restore, verify


def make_profile(root):
    (root / "history/days").mkdir(parents=True)
    (root / "history/raw").mkdir(parents=True)
    (root / "personalities").mkdir()
    (root / "config.toml").write_text('[safety]\nmode = "commits"\n')
    (root / "memory.md").write_text("- likes grilled cheese\n")
    (root / "secrets.toml").write_text('groq = "gsk-not-a-real-key"\n')
    (root / "api_token").write_text("tok-not-real")
    (root / "history/days/2026-10-11.md").write_text("A day summary.")
    (root / "history/raw/2026-10-11.jsonl").write_text('{"text": "private words"}\n')
    (root / "personalities/minty.md").write_text("You are Minty")
    db = sqlite3.connect(root / "memory.db")
    db.execute("create table items (id integer primary key, gist text)")
    db.execute("insert into items(gist) values ('Murphy turns 9 on 26 October')")
    db.commit()
    db.close()


def test_a_backup_holds_what_matters_and_never_the_keys(tmp_path):
    make_profile(tmp_path / "cfg")
    info = create(tmp_path / "cfg", b"correct horse", tmp_path / "out/minty-backup-20261014.bak")
    blob = info.path.read_bytes()
    assert blob.startswith(b"MINTYBK1") and b"grilled cheese" not in blob and b"Murphy" not in blob  # encrypted
    result = verify(info.path, b"correct horse")
    assert result.ok and result.files == info.files == 5  # config, memory.md, summary, personality, memory.db
    target = tmp_path / "restored"
    assert restore(info.path, b"correct horse", target) == 5
    assert (target / "memory.md").read_text() == "- likes grilled cheese\n"
    assert not (target / "secrets.toml").exists() and not (target / "api_token").exists()
    assert not (target / "history/raw").exists()  # raw transcripts only on request
    db = sqlite3.connect(target / "memory.db")
    assert db.execute("select gist from items").fetchone()[0] == "Murphy turns 9 on 26 October"


def test_raw_history_can_be_included_when_asked(tmp_path):
    make_profile(tmp_path / "cfg")
    info = create(tmp_path / "cfg", b"k", tmp_path / "a.bak", include_raw=True)
    assert verify(info.path, b"k").files == 6


def test_a_wrong_secret_or_a_changed_file_fails(tmp_path):
    make_profile(tmp_path / "cfg")
    info = create(tmp_path / "cfg", b"right", tmp_path / "a.bak")
    wrong = verify(info.path, b"wrong")
    assert not wrong.ok and "wrong passphrase" in wrong.problems[0]
    data = bytearray(info.path.read_bytes())
    data[200] ^= 0xFF
    info.path.write_bytes(bytes(data))
    assert not verify(info.path, b"right").ok
    junk = tmp_path / "junk.bak"
    junk.write_bytes(b"not a backup at all, just some bytes padding padding padding")
    assert "not a Minty backup" in verify(junk, b"x").problems[0]


def test_restore_never_overwrites_a_live_folder(tmp_path):
    make_profile(tmp_path / "cfg")
    info = create(tmp_path / "cfg", b"k", tmp_path / "a.bak")
    with pytest.raises(BackupError, match="not empty"):
        restore(info.path, b"k", tmp_path / "cfg")


def test_the_key_file_is_made_once_with_private_permissions(tmp_path):
    first = load_or_make_keyfile(tmp_path / "backup.key")
    assert len(first) == 48 and load_or_make_keyfile(tmp_path / "backup.key") == first
    assert oct((tmp_path / "backup.key").stat().st_mode & 0o777) == "0o600"
    make_profile(tmp_path / "cfg")
    info = create(tmp_path / "cfg", first, tmp_path / "k.bak")
    assert verify(info.path, first).ok


def test_only_the_newest_seven_are_kept(tmp_path):
    for i in range(10):
        (tmp_path / f"minty-backup-2026101{i}.bak").write_bytes(b"x")
    assert len(prune(tmp_path)) == 3 and len(list(tmp_path.glob("*.bak"))) == 7


def test_what_minty_knows_is_backed_up_too_the_second_brain_its_originals_the_world_model_and_the_workflows(tmp_path):
    cfg = tmp_path / "cfg"
    make_profile(cfg)
    from minty_core.knowledge.store import KnowledgeStore
    from minty_core.mind.world import WorldStore

    kb = KnowledgeStore(cfg / "knowledge.sqlite", originals=cfg / "knowledge-originals")
    src = kb.add_source("pdf", "the contract says ninety days", name="contract.pdf", data=b"%PDF the real bytes", origin="outside")
    kb.add_item("finding", "Payment terms are ninety days", "x", sources=[src["id"]])
    kb.close()
    WorldStore(cfg / "world.sqlite").note("organization", "Acme", "size", "50 people", "told", "owner")
    info = create(cfg, b"k", tmp_path / "a.bak")
    target = tmp_path / "restored"
    restore(info.path, b"k", target)
    db = sqlite3.connect(target / "knowledge.sqlite")
    assert db.execute("select title from items").fetchone()[0] == "Payment terms are ninety days"
    assert (target / "knowledge-originals" / src["sha256"][:2] / src["sha256"]).read_bytes() == b"%PDF the real bytes"  # the original, byte for byte
    assert sqlite3.connect(target / "world.sqlite").execute("select count(*) from entities").fetchone()[0] == 1
