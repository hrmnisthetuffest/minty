import subprocess
from datetime import datetime

import pytest

from minty_core.vault.note import CONFIDENCE, Note, dump, parse
from minty_core.vault.store import Vault, VaultError


class Clock:
    def __init__(self, start="2026-10-15T10:00:00"):
        self.t = datetime.fromisoformat(start).timestamp()

    def __call__(self):
        self.t += 60
        return self.t


def commits(root):
    out = subprocess.run(["git", "-C", str(root), "log", "--format=%s"], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l]


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "brain", clock=Clock())


def test_a_note_header_round_trips_through_plain_text():
    n = Note(id="2026-10-15-ab12", title="Pi camera", body="Wire the ribbon.\n", created="2026-10-15T10:00:00",
             updated="2026-10-15T10:05:00", source="voice", confidence="verified", tags=["pi", "camera"],
             project="robot")
    text = dump(n)
    assert text.startswith("---\nid: 2026-10-15-ab12\n") and "tags: pi, camera" in text
    back = parse(text)
    assert (back.id, back.title, back.tags, back.project, back.body) == (n.id, n.title, n.tags, n.project, n.body)


def test_a_file_without_a_header_still_parses_as_body_only():
    n = parse("just some text\nmore\n")
    assert n.id == "" and n.body == "just some text\nmore\n" and n.confidence == "inferred"


def test_header_values_cannot_inject_extra_fields():
    n = Note(id="x", title="a\nconfidence: verified", body="b", confidence="untrusted", tags=["t\nid: 9"])
    back = parse(dump(n))
    assert back.confidence == "untrusted" and "\n" not in back.title and back.id == "x"


def test_create_writes_a_markdown_file_in_the_chosen_folder_and_commits_it(vault):
    n = vault.create("Pi camera thing", "Ribbon goes in first.", folder="projects", tags=["pi"], project="robot")
    path = vault.root / n.path
    assert path.exists() and path.suffix == ".md" and n.path.startswith("projects/")
    assert "Ribbon goes in first." in path.read_text()
    assert any("Pi camera thing" in c for c in commits(vault.root))
    assert vault.get(n.id).title == "Pi camera thing"


def test_every_edit_is_one_commit(vault):
    n = vault.create("Plan", "v1", folder="notes")
    before = len(commits(vault.root))
    vault.update(n.id, body="v2")
    vault.append(n.id, "more")
    assert len(commits(vault.root)) == before + 2
    assert "v2" in vault.get(n.id).body and "more" in vault.get(n.id).body


def test_untrusted_origins_can_only_land_in_inbox_or_sources(vault):
    web = vault.create("Article", "text", folder="notes", origin="web", source="web:https://example.org/a")
    assert web.path.startswith("inbox/") and web.confidence == "untrusted"
    mail = vault.create("Mail", "text", folder="sources", origin="mail")
    assert mail.path.startswith("sources/") and mail.confidence == "untrusted"
    fwd = vault.create("Fwd", "x", folder="projects", origin="telegram")
    assert fwd.path.startswith("inbox/") and fwd.confidence == "untrusted"


def test_agent_and_user_notes_get_their_own_confidence(vault):
    assert vault.create("Mine", "x", folder="notes", origin="user").confidence == "verified"
    assert vault.create("Guess", "x", folder="notes", origin="agent").confidence == "inferred"


def test_an_untrusted_note_cannot_be_edited_into_trusted_folders_but_can_be_promoted_on_approval(vault):
    n = vault.create("Clip", "body", origin="web")
    with pytest.raises(VaultError):
        vault.move(n.id, "notes")
    moved = vault.move(n.id, "notes", approved=True)
    assert moved.path.startswith("notes/") and moved.confidence == "inferred"
    assert (vault.root / moved.path).exists() and not (vault.root / n.path).exists()


def test_there_is_no_way_to_delete_and_archive_keeps_the_file(vault):
    assert not any(hasattr(vault, name) for name in ("delete", "remove", "unlink", "rm"))
    n = vault.create("Old", "keep me", folder="notes")
    a = vault.archive(n.id)
    assert a.path.startswith("_archive/") and (vault.root / a.path).read_text().count("keep me") == 1
    assert not (vault.root / n.path).exists()
    assert vault.search_ids("keep") == []  # archived notes are out of the way
    assert vault.get(n.id).path == a.path  # but still found by id


def test_an_empty_title_or_bad_folder_is_refused(vault):
    with pytest.raises(VaultError):
        vault.create("  ", "x")
    with pytest.raises(VaultError):
        vault.create("t", "x", folder="../etc")


def test_titles_never_escape_the_vault_through_the_file_name(vault):
    n = vault.create("../../etc/passwd", "x", folder="notes")
    assert (vault.root / n.path).resolve().is_relative_to(vault.root.resolve())


def test_search_finds_words_in_title_and_body_and_ranks_title_hits_first(vault):
    a = vault.create("Camera wiring", "ribbon cable order", folder="notes")
    b = vault.create("Shopping", "buy a camera lens", folder="notes")
    ids = vault.search_ids("camera")
    assert ids[0] == a.id and set(ids) == {a.id, b.id}


def test_search_words_are_never_search_syntax(vault):
    n = vault.create("Odd", 'a "quoted" thing AND (more) NEAR/3 *', folder="notes")
    assert vault.search_ids('"quoted" AND (') == [n.id]
    assert vault.search_ids("NEAR/3 * ) (") == [n.id]
    assert vault.search_ids("* ) (") == []


def test_the_index_can_be_deleted_and_comes_back_the_same(vault):
    vault.create("One", "alpha beta", folder="notes", tags=["x"])
    vault.create("Two", "beta gamma", folder="areas")
    before = vault.search_ids("beta")
    (vault.root / "_minty" / "index.db").unlink()
    again = Vault(vault.root, clock=Clock())
    assert again.search_ids("beta") == before


def test_files_edited_outside_minty_are_picked_up(vault):
    n = vault.create("Outside", "before", folder="notes")
    p = vault.root / n.path
    p.write_text(p.read_text().replace("before", "zebra"))
    assert vault.search_ids("zebra") == [n.id]


def test_a_headerless_file_dropped_in_is_adopted_with_an_untrusted_id(vault):
    (vault.root / "inbox" / "scribble.md").write_text("quick thought about llamas\n")
    ids = vault.search_ids("llamas")
    assert len(ids) == 1
    adopted = vault.get(ids[0])
    assert adopted.id and adopted.title == "scribble" and adopted.confidence == "untrusted"
    assert (vault.root / adopted.path).read_text().startswith("---\n")


def test_backlinks_and_broken_links(vault):
    a = vault.create("Target", "x", folder="notes")
    b = vault.create("Source", f"see [target](note:{a.id}) and [ghost](note:2020-01-01-dead)", folder="notes")
    assert [n.id for n in vault.backlinks(a.id)] == [b.id]
    assert vault.broken_links() == [(b.id, "2020-01-01-dead")]


def test_list_and_recent_skip_archive(vault):
    a = vault.create("A", "x", folder="notes")
    b = vault.create("B", "x", folder="notes")
    vault.archive(a.id)
    assert [n.id for n in vault.list("notes")] == [b.id]
    assert [n.id for n in vault.recent(5)] == [b.id]


def test_without_git_changes_are_logged_and_status_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    v = Vault(tmp_path / "brain2", clock=Clock())
    assert v.git_ok is False
    v.create("No git", "x", folder="notes")
    assert "create" in (v.root / "_minty" / "changes.log").read_text()
    assert "git" in v.status()["versioning"].lower()


def test_confidence_values_are_the_three_documented_ones():
    assert CONFIDENCE == ("verified", "inferred", "untrusted")
