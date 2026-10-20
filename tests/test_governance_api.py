"""The memory audit over the control API (the panel's Memory audit page) and the Sunday 'what I learned' notice."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.context import build_context
from minty_core.governance.learned import learned_notice


@pytest.fixture
def ctx(tmp_path):
    return build_context(tmp_path / "cfg")


@pytest.fixture
def client(ctx):
    return TestClient(create_app(ctx))


@pytest.fixture
def auth(ctx):
    return {"Authorization": f"Bearer {ctx.token}"}


def test_the_audit_api_needs_the_token(client):
    assert client.get("/audit").status_code == 401


def test_list_edit_and_forget_one_entry(client, auth, ctx):
    gid = ctx.services["gists"].add("fact", "School bus leaves at 7:15", source_ref="remember")
    ctx.memory.add("Prefers metric units")
    rows = client.get("/audit", headers=auth).json()["entries"]
    assert {r["text"] for r in rows} == {"School bus leaves at 7:15", "Prefers metric units"}
    assert {"id", "store", "source", "date", "confidence"} <= set(rows[0])
    put = client.put(f"/audit/fact:{gid}", headers=auth, json={"text": "School bus leaves at 7:30"})
    assert put.status_code == 200 and put.json()["text"] == "School bus leaves at 7:30"
    assert client.put(f"/audit/fact:{gid}", headers=auth, json={"text": "my password is x"}).status_code == 422
    assert client.delete(f"/audit/fact:{gid}", headers=auth).status_code == 204
    assert client.delete(f"/audit/fact:{gid}", headers=auth).status_code == 404
    assert client.get("/audit", headers=auth, params={"query": "metric"}).json()["entries"][0]["id"] == "line:0"


def test_the_sunday_notice_is_sent_once_and_only_when_on(tmp_path):
    from minty_core.gists import GistIndex
    from minty_core.governance.audit import MemoryAudit
    from minty_core.memory import MemoryStore

    sunday = datetime(2026, 10, 18, 19, 0)
    gists = GistIndex(tmp_path / "m.db", clock=lambda: sunday.timestamp())
    gists.add("fact", "Likes mango lassi", source_ref="remember", created=sunday.timestamp() - 86400)
    audit = MemoryAudit(MemoryStore(tmp_path / "memory.md"), gists, None, clock=lambda: sunday.timestamp())
    assert learned_notice(audit, sunday, "", enabled=False) == (None, "")
    text, day = learned_notice(audit, sunday, "", enabled=True)
    assert "Likes mango lassi" in text and day == "2026-10-18"
    assert learned_notice(audit, sunday, day, enabled=True) == (None, day)
    monday = datetime(2026, 10, 19, 19, 0)
    assert learned_notice(audit, monday, "", enabled=True) == (None, "")
    morning = datetime(2026, 10, 18, 10, 0)
    assert learned_notice(audit, morning, "", enabled=True) == (None, "")
