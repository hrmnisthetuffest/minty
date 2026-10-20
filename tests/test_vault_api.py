"""The vault viewer/editor's API: list, search, open with backlinks, edit, create, archive, filing suggestions."""
import pytest
from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.context import build_context


@pytest.fixture
def ctx(tmp_path):
    c = build_context(tmp_path / "cfg")
    data = c.store.config.model_dump(mode="json")
    data["vault"].update(enabled=True, path=str(tmp_path / "brain"), git=False)
    c.store.update(data)
    return c


@pytest.fixture
def client(ctx):
    return TestClient(create_app(ctx))


@pytest.fixture
def auth(ctx):
    return {"Authorization": f"Bearer {ctx.token}"}


def test_the_vault_api_needs_the_token(client):
    assert client.get("/vault").status_code == 401


def test_it_says_when_the_vault_is_off(tmp_path):
    c = build_context(tmp_path / "cfg2")
    r = TestClient(create_app(c)).get("/vault", headers={"Authorization": f"Bearer {c.token}"})
    assert r.status_code == 503 and "switched off" in r.json()["detail"]
    assert not (tmp_path / "brain").exists()


def test_create_open_edit_and_find_a_note(client, auth):
    made = client.post("/vault/notes", headers=auth, json={"title": "Pi camera", "text": "ribbon first",
                                                           "folder": "notes", "tags": ["pi"]})
    assert made.status_code == 201
    nid = made.json()["id"]
    got = client.get(f"/vault/notes/{nid}", headers=auth).json()
    assert got["title"] == "Pi camera" and got["body"].startswith("ribbon first") and got["confidence"] == "verified"
    put = client.put(f"/vault/notes/{nid}", headers=auth, json={"text": "ribbon then lens", "tags": ["pi", "lens"]})
    assert put.status_code == 200 and put.json()["tags"] == ["pi", "lens"]
    hits = client.get("/vault/search", headers=auth, params={"q": "lens"}).json()["hits"]
    assert [h["id"] for h in hits] == [nid] and "[your note" in hits[0]["badge"]


def test_backlinks_come_with_the_note(client, auth):
    a = client.post("/vault/notes", headers=auth, json={"title": "Target", "text": "x", "folder": "notes"}).json()
    b = client.post("/vault/notes", headers=auth, json={"title": "Linker", "folder": "notes",
                                                        "text": f"see [t](note:{a['id']})"}).json()
    got = client.get(f"/vault/notes/{a['id']}", headers=auth).json()
    assert got["backlinks"] == [{"id": b["id"], "title": "Linker"}]


def test_archiving_hides_a_note_but_keeps_it(client, auth):
    n = client.post("/vault/notes", headers=auth, json={"title": "Old", "text": "keep", "folder": "notes"}).json()
    assert client.post(f"/vault/notes/{n['id']}/archive", headers=auth).status_code == 200
    assert client.get("/vault/search", headers=auth, params={"q": "keep"}).json()["hits"] == []
    assert client.get(f"/vault/notes/{n['id']}", headers=auth).json()["path"].startswith("_archive/")
    methods = {m for r in client.app.routes if getattr(r, "path", "").startswith("/vault")
               for m in getattr(r, "methods", ())}
    assert "DELETE" not in methods


def test_a_missing_note_is_a_404_and_a_bad_folder_a_422(client, auth):
    assert client.get("/vault/notes/nope", headers=auth).status_code == 404
    r = client.post("/vault/notes", headers=auth, json={"title": "x", "text": "y", "folder": "../etc"})
    assert r.status_code == 422


def test_filing_suggestions_can_be_approved_or_rejected_from_the_viewer(client, auth, ctx):
    client.post("/vault/notes", headers=auth, json={"title": "Robot arm", "text": "servo", "folder": "projects",
                                                    "project": "robot", "tags": ["servo"]})
    idea = client.post("/vault/notes", headers=auth, json={"title": "Servo idea", "text": "servo for the robot"}).json()
    overview = client.get("/vault", headers=auth).json()
    assert [p["note_id"] for p in overview["proposals"]] == [idea["id"]]
    assert overview["proposals"][0]["folder"] == "projects"
    done = client.post(f"/vault/inbox/{idea['id']}/approve", headers=auth)
    assert done.status_code == 200 and done.json()["path"].startswith("projects/")
    assert client.post(f"/vault/inbox/{idea['id']}/approve", headers=auth).status_code == 409


def test_search_can_be_limited_by_date(client, auth):
    client.post("/vault/notes", headers=auth, json={"title": "Today note", "text": "lens", "folder": "notes"})
    r = client.get("/vault/search", headers=auth, params={"q": "lens", "until": "2000-01-01"})
    assert r.json()["hits"] == []


def test_private_area_over_the_api_unlock_add_open_lock(client, auth, ctx, tmp_path):
    data = ctx.store.config.model_dump(mode="json")
    data["vault"]["encryption"] = True
    ctx.store.update(data)
    status = client.get("/vault/private", headers=auth).json()
    assert status == {"exists": False, "unlocked": False, "notes": []}
    ctx.services["vault"].kit().private.create("a long enough passphrase")
    assert client.post("/vault/private/unlock", headers=auth, json={"passphrase": "wrong one here"}).status_code == 401
    ok = client.post("/vault/private/unlock", headers=auth, json={"passphrase": "a long enough passphrase"})
    assert ok.status_code == 200
    made = client.post("/vault/private/notes", headers=auth, json={"title": "Worry", "text": "exam fears"})
    assert made.status_code == 201
    nid = made.json()["id"]
    assert client.get(f"/vault/private/notes/{nid}", headers=auth).json()["body"] == "exam fears"
    listed = client.get("/vault/private", headers=auth).json()
    assert listed["unlocked"] and [n["title"] for n in listed["notes"]] == ["Worry"]
    assert client.post("/vault/private/lock", headers=auth).status_code == 204
    assert client.get(f"/vault/private/notes/{nid}", headers=auth).status_code == 423
    assert client.get("/vault/search", headers=auth, params={"q": "exam"}).json()["hits"] == []


def test_private_api_is_off_without_the_switch(client, auth):
    assert client.get("/vault/private", headers=auth).status_code == 503
