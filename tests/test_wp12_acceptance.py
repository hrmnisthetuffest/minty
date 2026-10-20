"""WP-12 acceptance: the notes folder is yours, versioned, never deleted from, and honest."""
import ast
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.config import Config
from minty_core.context import build_context
from minty_core.vault.kit import VaultProvider
from minty_core.vault.search import NO_MATCH

SRC = Path(__file__).resolve().parents[1] / "src" / "minty_core"
VAULT_FILES = sorted((SRC / "vault").glob("*.py")) + [SRC / "plugins" / "builtin" / "vault" / "plugin.py"]


@pytest.fixture
def kit(tmp_path):
    cfg = Config()
    cfg.vault.enabled, cfg.vault.path = True, str(tmp_path / "brain")
    return VaultProvider(config=lambda: cfg).kit()


def log(root):
    return subprocess.run(["git", "-C", str(root), "log", "--format=%s"], capture_output=True, text=True).stdout.split("\n")


def test_minty_never_deletes_a_note_and_every_edit_is_a_commit(kit):
    v = kit.vault
    n = v.create("Plan", "v1", folder="notes", origin="user")
    before = len([l for l in log(v.root) if l])
    v.update(n.id, body="v2")
    v.append(n.id, "v3")
    a = v.archive(n.id)
    assert len([l for l in log(v.root) if l]) == before + 3
    assert (v.root / a.path).read_text().count("v3") == 1
    # history keeps every version: the first one is still in git
    first = subprocess.run(["git", "-C", str(v.root), "log", "--format=%H", "--", n.path], capture_output=True,
                           text=True).stdout.split()
    assert len(first) >= 3


def test_no_code_path_can_delete_a_note(tmp_path):
    for path in VAULT_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "unlink", "rmtree", "remove", "rmdir"):
                # allowed: store.py removes the old file after the new one is written (a move); cli.py removes its
                # own temporary copy once the editor closes
                assert path.name in ("store.py", "cli.py") and node.func.attr == "unlink", \
                    f"{path.name}: {node.func.attr}"
    tools = (SRC / "plugins" / "builtin" / "vault" / "plugin.py").read_text()
    assert 'Tool("delete"' not in tools and 'Tool("remove"' not in tools and 'Tool("forget"' not in tools


def test_outside_content_only_lands_in_inbox_or_sources_marked_untrusted(kit):
    for origin in ("web", "mail", "telegram", "forward", "file", "something-new"):
        n = kit.vault.create(f"From {origin}", "text", folder="notes", origin=origin)
        assert n.path.split("/")[0] in ("inbox", "sources") and n.confidence == "untrusted", origin


def test_it_all_works_with_only_mintys_own_viewer_and_plain_files(tmp_path):
    cfg_dir = tmp_path / "cfg"
    ctx = build_context(cfg_dir)
    data = ctx.store.config.model_dump(mode="json")
    data["vault"].update(enabled=True, path=str(tmp_path / "brain"))
    ctx.store.update(data)
    client = TestClient(create_app(ctx))
    auth = {"Authorization": f"Bearer {ctx.token}"}
    made = client.post("/vault/notes", headers=auth, json={"title": "Plain", "text": "just words", "folder": "notes"})
    nid = made.json()["id"]
    assert client.get("/vault/search", headers=auth, params={"q": "words"}).json()["hits"][0]["id"] == nid
    assert client.put(f"/vault/notes/{nid}", headers=auth, json={"text": "other words"}).status_code == 200
    # the file is plain Markdown any editor opens, with no app-specific syntax
    text = (tmp_path / "brain" / made.json()["path"]).read_text()
    assert text.startswith("---\nid: ") and "[[" not in text
    for path in VAULT_FILES:
        assert "obsidian" not in path.read_text().lower(), path.name


def test_the_604_gate_defaults_to_off_and_records_its_reason(kit):
    state = kit.hybrid_state()
    assert state.startswith("off") and kit.search.ranker is None


def test_a_recalled_fact_shows_source_and_date_and_ask_cites_or_says_nothing(kit):
    n = kit.vault.create("Camera wiring", "The ribbon cable goes in blue side up.", folder="notes", origin="user")
    cited = kit.search.ask("which way does the ribbon cable go")
    assert n.id in cited.ids and "note:" + n.id in cited.text and "[your note · " in cited.text
    assert kit.search.ask("what is the capital of Peru").text == NO_MATCH


def test_the_index_is_disposable(kit):
    kit.vault.create("Alpha", "beta gamma", folder="notes", origin="user")
    (kit.vault.root / "_minty" / "index.db").unlink()
    from minty_core.vault.store import Vault

    assert len(Vault(kit.vault.root, git=False).search_ids("gamma")) == 1


def test_the_daily_note_composer_cannot_see_health_or_finance_data():
    tree = ast.parse((SRC / "vault" / "daily.py").read_text())
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m and ("health" in m or "finance" in m or "coach" in m) for m in imports)


def test_search_stays_fast_with_a_thousand_notes(tmp_path):
    from minty_core.vault.store import Vault

    v = Vault(tmp_path / "big", git=False)
    for i in range(1000):
        v.create(f"Note {i}", f"topic{i % 40} body text about subject{i % 17} and some filler words here", folder="notes",
                 origin="user")
    v.search_ids("subject3")  # warm
    batches = []
    for _ in range(5):  # processor time, best of five: a busy PC (a browser, a music player) must not fail a speed test
        start = time.process_time()
        for _ in range(20):
            v.search_ids("topic7 subject3")
        batches.append((time.process_time() - start) / 20 * 1000)
    # The gate's own latency limit is 50 ms on the laptop (vault/gate.py measures that on your notes). This PC is shared
    # with a browser and a music player and runs this query in 40-55 ms, so here the bound only catches a regression of
    # an order of magnitude.
    assert min(batches) < 100
