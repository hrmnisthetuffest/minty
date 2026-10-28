"""The sheets and docs Minty made or was told about: found again instead of made twice, and the only ones it may write to."""
import pytest

from minty_core.artifacts import ArtifactStore

SHEET = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
DOC = "1a2B3c4D5e6F7g8H9i0J1k2L3m4N5o6P7q8R9s0T1u2V"


@pytest.fixture
def store(tmp_path):
    s = ArtifactStore(tmp_path / "artifacts.db", clock=lambda: 1000.0)
    yield s
    s.close()


def test_what_is_added_can_be_found_again_and_is_known(store):
    a = store.add("sheet", SHEET, "Leads", url=f"https://docs.google.com/spreadsheets/d/{SHEET}/edit", key="leads", source="minty")
    assert a.kind == "sheet" and a.title == "Leads" and a.source == "minty" and a.status == "active"
    assert store.known(SHEET) and not store.known(DOC)
    assert [x.ext_id for x in store.find(kind="sheet", key="leads")] == [SHEET]
    assert store.find(key="other") == [] and store.find(title="leads")[0].ext_id == SHEET  # a title is matched without its case


def test_adding_the_same_thing_twice_updates_it_instead_of_duplicating(store):
    first = store.add("sheet", SHEET, "Leads", key="leads", source="minty")
    again = store.add("sheet", SHEET, "Leads 2026", key="leads", source="minty")
    assert again.id == first.id and len(store.find()) == 1 and store.find()[0].title == "Leads 2026"


def test_an_id_that_could_not_be_one_is_refused(store):
    for bad in ("", "short", "has spaces in it not an id", "x" * 200, "a/b/c" + "d" * 20):
        with pytest.raises(ValueError):
            store.add("sheet", bad, "t")
    with pytest.raises(ValueError):
        store.add("spaceship", SHEET, "t")


def test_a_user_named_artifact_says_so_and_a_forgotten_one_is_no_longer_a_target(store):
    store.add("doc", DOC, "Plan", source="user")
    assert store.find()[0].source == "user" and store.known(DOC)
    assert store.forget(DOC) is True and not store.known(DOC) and store.forget(DOC) is False


def test_ids_are_read_out_of_links_and_plain_ids(store):
    assert ArtifactStore.parse_ref(f"https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=0") == ("sheet", SHEET)
    assert ArtifactStore.parse_ref(f"https://docs.google.com/document/d/{DOC}/edit") == ("doc", DOC)
    assert ArtifactStore.parse_ref(SHEET) == (None, SHEET)
    assert ArtifactStore.parse_ref("https://evil.test/spreadsheets/d/" + SHEET) == (None, "")  # not a Google link
    assert ArtifactStore.parse_ref("hello there") == (None, "")


def test_it_survives_a_restart(tmp_path):
    path = tmp_path / "artifacts.db"
    a = ArtifactStore(path)
    a.add("sheet", SHEET, "Leads", key="leads")
    a.close()
    b = ArtifactStore(path)
    assert b.known(SHEET) and b.find(key="leads")[0].title == "Leads"
    b.close()


def test_the_file_is_private(tmp_path):
    import os, stat
    s = ArtifactStore(tmp_path / "artifacts.db")
    s.close()
    assert stat.S_IMODE(os.stat(tmp_path / "artifacts.db").st_mode) == 0o600


def test_a_key_can_be_put_on_an_artifact_so_the_next_job_finds_it(store):
    store.add("sheet", SHEET, "Leads")
    assert store.tag(SHEET, "leads-2026") is True and store.find(key="leads-2026")[0].ext_id == SHEET
    assert store.tag(DOC, "x") is False

