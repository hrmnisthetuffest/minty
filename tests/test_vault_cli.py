"""The terminal view of the vault: `minty-core notes ...` (list, search, show, new, edit, inbox, check)."""
import io

import pytest

from minty_core.config import Config
from minty_core.vault.cli import run
from minty_core.vault.kit import VaultProvider


@pytest.fixture
def provider(tmp_path):
    cfg = Config()
    cfg.vault.enabled, cfg.vault.path, cfg.vault.git = True, str(tmp_path / "brain"), False
    return VaultProvider(config=lambda: cfg)


def go(provider, *argv, stdin="", editor=None):
    out = io.StringIO()
    code = run(list(argv), provider, out=out, stdin=io.StringIO(stdin), editor=editor)
    return code, out.getvalue()


def test_new_reads_the_text_from_input_and_list_shows_it(provider):
    code, out = go(provider, "new", "Pi camera", "--folder", "notes", stdin="Ribbon goes in first.\n")
    assert code == 0 and "note:" in out
    code, out = go(provider, "list")
    assert code == 0 and "Pi camera" in out and "notes/" in out


def test_search_and_show(provider):
    go(provider, "new", "Camera wiring", "--folder", "notes", stdin="the ribbon cable\n")
    code, out = go(provider, "search", "ribbon")
    assert code == 0 and "Camera wiring" in out and "[your note" in out
    ident = provider.kit().vault.list("notes")[0].id
    code, shown = go(provider, "show", ident)
    assert code == 0 and "the ribbon cable" in shown and "Camera wiring" in shown


def test_show_of_an_unknown_id_fails_politely(provider):
    code, out = go(provider, "show", "nope")
    assert code == 1 and "No note with that id" in out


def test_edit_uses_the_editor_and_saves_a_change(provider):
    go(provider, "new", "Plan", "--folder", "notes", stdin="v1\n")
    ident = provider.kit().vault.list("notes")[0].id
    code, out = go(provider, "edit", ident, editor=lambda text: text.replace("v1", "v2"))
    assert code == 0 and "Saved" in out
    assert "v2" in provider.kit().vault.get(ident).body


def test_an_unchanged_edit_saves_nothing(provider):
    go(provider, "new", "Plan", "--folder", "notes", stdin="v1\n")
    ident = provider.kit().vault.list("notes")[0].id
    code, out = go(provider, "edit", ident, editor=lambda text: text)
    assert code == 0 and "No changes" in out


def test_inbox_lists_suggestions_and_approve_files_the_note(provider):
    go(provider, "new", "Robot arm", "--folder", "projects", "--project", "robot", "--tag", "servo", stdin="x\n")
    go(provider, "new", "Servo idea", stdin="servo for the robot\n")
    code, out = go(provider, "inbox")
    assert "Servo idea" in out and "projects" in out
    ident = provider.kit().vault.list("inbox")[0].id
    code, out = go(provider, "approve", ident)
    assert code == 0 and "projects" in out


def test_check_reports_broken_links_and_versioning(provider):
    go(provider, "new", "A", "--folder", "notes", stdin="see [ghost](note:2020-01-01-dead)\n")
    code, out = go(provider, "check")
    assert code == 0 and "1 broken link" in out and "no git" in out.lower()


def test_when_the_vault_is_off_it_says_how_to_turn_it_on(tmp_path):
    cfg = Config()
    cfg.vault.path = str(tmp_path / "brain")
    code, out = go(VaultProvider(config=lambda: cfg), "list")
    assert code == 1 and "vault.enabled" in out and not (tmp_path / "brain").exists()


def test_gate_runs_the_questions_records_the_result_and_says_what_it_means(provider, tmp_path):
    import json

    for title, body in [("Camera wiring", "ribbon cable goes in first before the lens mount"),
                        ("Servo torque", "stall torque of the servo at six volts"),
                        ("School timetable", "maths on monday physics on thursday")]:
        go(provider, "new", title, "--folder", "notes", stdin=body + "\n")
    q = tmp_path / "q.json"
    q.write_text(json.dumps([{"q": "camra ribon cabel", "note": "Camera wiring"},
                             {"q": "servoo torqe", "note": "Servo torque"},
                             {"q": "scool timetabel", "note": "School timetable"}]))
    code, out = go(provider, "gate", str(q), "--min", "3")
    assert code == 0 and "Passed" in out and "gain" in out
    assert (provider.kit().vault.root / "_minty" / "gate.json").exists()
    code, out = go(provider, "gate", str(q))  # the real gate needs 100 questions
    assert code == 0 and "Not passed" in out and "100 questions" in out


def test_private_create_asks_twice_prints_the_recovery_phrase_once_and_stores_nothing_plain(provider, tmp_path):
    answers = iter(["a long enough passphrase", "a long enough passphrase"])
    out = io.StringIO()
    cfg_on = provider._config()
    cfg_on.vault.encryption = False
    code = run(["private-create"], provider, out=out, stdin=io.StringIO(""), ask_secret=lambda prompt: next(answers))
    assert code == 1 and "vault.encryption" in out.getvalue()
    cfg_on.vault.encryption = True
    out = io.StringIO()
    code = run(["private-create"], provider, out=out, stdin=io.StringIO(""), ask_secret=lambda prompt: next(answers))
    text = out.getvalue()
    assert code == 0 and "recovery phrase" in text.lower() and "lose" in text.lower()
    assert provider.kit().private.exists()
    assert "a long enough passphrase" not in text
    again = io.StringIO()
    assert run(["private-create"], provider, out=again, stdin=io.StringIO(""),
               ask_secret=lambda p: "x") == 1 and "already" in again.getvalue()


def test_private_create_refuses_a_passphrase_that_does_not_match(provider):
    answers = iter(["a long enough passphrase", "something else entirely"])
    cfg = provider._config()
    cfg.vault.encryption = True
    out = io.StringIO()
    code = run(["private-create"], provider, out=out, stdin=io.StringIO(""), ask_secret=lambda p: next(answers))
    assert code == 1 and "did not match" in out.getvalue() and not provider.kit().private.exists()
