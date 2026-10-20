"""612: an encrypted notes area. Unreadable and unindexed while locked; the passphrase is never stored."""
import json

import pytest

from minty_core.vault.private import Locked, PrivateArea, PrivateError, WrongPassphrase
from minty_core.vault.store import Vault


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def area(tmp_path):
    return PrivateArea(tmp_path / "brain", clock=Clock(), auto_lock_s=600)


def make(area, passphrase="correct horse battery"):
    return area.create(passphrase)


def test_creating_returns_a_recovery_phrase_once_and_files_hold_no_plaintext(area, tmp_path):
    phrase = make(area)
    assert len(phrase.split("-")) == 4 and area.exists()
    area.unlock("correct horse battery")
    n = area.add("Therapy notes", "I felt anxious about the exam results", tags=["health"])
    blobs = b"".join(p.read_bytes() for p in (tmp_path / "brain" / "_private").rglob("*") if p.is_file())
    for secret in (b"Therapy", b"anxious", b"exam results", b"correct horse", phrase.encode()):
        assert secret not in blobs
    assert n.startswith("p-")


def test_it_cannot_be_created_twice(area):
    make(area)
    with pytest.raises(PrivateError):
        area.create("another one")


def test_locked_means_nothing_can_be_read_listed_or_searched(area):
    make(area)
    with pytest.raises(Locked):
        area.add("x", "y")
    with pytest.raises(Locked):
        area.list()
    with pytest.raises(Locked):
        area.search("anything")
    assert area.unlocked is False


def test_wrong_passphrase_fails_and_the_right_one_opens(area):
    make(area)
    with pytest.raises(WrongPassphrase):
        area.unlock("nope")
    area.unlock("correct horse battery")
    assert area.unlocked


def test_add_get_search_update_while_unlocked(area):
    make(area)
    area.unlock("correct horse battery")
    a = area.add("Exam worries", "afraid of the physics paper", tags=["school"])
    area.add("Birthday plan", "surprise for dad")
    assert {t for _, t, _ in area.list()} == {"Birthday plan", "Exam worries"}
    assert area.get(a)["body"] == "afraid of the physics paper"
    assert [h["id"] for h in area.search("physics paper")] == [a]
    area.update(a, body="less afraid now")
    assert area.get(a)["body"] == "less afraid now"
    assert [h["id"] for h in area.search("less afraid")] == [a] and area.search("physics paper") == []


def test_locking_forgets_the_key_and_the_in_memory_index(area):
    make(area)
    area.unlock("correct horse battery")
    area.add("Secret", "alpha beta gamma")
    area.lock()
    assert area.unlocked is False
    with pytest.raises(Locked):
        area.search("alpha")
    area.unlock("correct horse battery")
    assert len(area.search("alpha")) == 1  # it is all still there, decrypted again


def test_it_locks_itself_after_idle_time(area):
    make(area)
    area.unlock("correct horse battery")
    area._clock.t += 601
    assert area.unlocked is False
    area.unlock("correct horse battery")
    area._clock.t += 300
    area.list()  # use resets the idle timer
    area._clock.t += 400
    assert area.unlocked is True


def test_the_recovery_phrase_opens_it_and_a_new_passphrase_can_be_set(area):
    phrase = make(area)
    area.unlock("correct horse battery")
    n = area.add("Note", "body text here")
    area.lock()
    area.unlock(phrase)
    assert area.get(n)["title"] == "Note"
    area.change_passphrase("correct horse battery", "a brand new passphrase")
    area.lock()
    with pytest.raises(WrongPassphrase):
        area.unlock("correct horse battery")
    area.unlock("a brand new passphrase")
    assert area.get(n)["body"] == "body text here"


def test_a_damaged_or_swapped_file_is_reported_not_trusted(area, tmp_path):
    make(area)
    area.unlock("correct horse battery")
    a = area.add("One", "first body")
    b = area.add("Two", "second body")
    pa = tmp_path / "brain" / "_private" / f"{a}.enc"
    pb = tmp_path / "brain" / "_private" / f"{b}.enc"
    pa.write_bytes(pb.read_bytes())  # swapped contents: the id is bound into the encryption
    area.lock()
    area.unlock("correct horse battery")
    with pytest.raises(PrivateError):
        area.get(a)
    assert area.get(b)["body"] == "second body"
    pb.write_bytes(pb.read_bytes()[:-3] + b"xyz")
    with pytest.raises(PrivateError):
        area.get(b)


def test_repeated_wrong_guesses_lock_it_out_for_a_while(area):
    make(area)
    for _ in range(5):
        with pytest.raises(WrongPassphrase):
            area.unlock("guess")
    with pytest.raises(PrivateError, match="wait"):
        area.unlock("correct horse battery")
    area._clock.t += 61
    area.unlock("correct horse battery")


def test_the_notes_folder_search_never_sees_private_notes(area, tmp_path):
    make(area)
    area.unlock("correct horse battery")
    area.add("Hidden thing", "uniqueword zyxwvu")
    vault = Vault(tmp_path / "brain", git=False)
    assert vault.search_ids("zyxwvu") == [] and vault.search_ids("Hidden") == []
    assert all(n.folder != "_private" for n in vault.list(None))


def test_archiving_keeps_the_encrypted_file(area, tmp_path):
    make(area)
    area.unlock("correct horse battery")
    a = area.add("Old", "text of the old note")
    area.archive(a)
    assert area.list() == []
    assert any((tmp_path / "brain" / "_private").glob(f"{a}*"))
