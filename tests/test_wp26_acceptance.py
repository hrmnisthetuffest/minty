"""WP-26 acceptance (docs/plan-master.md): a domain from the framework passes the safety tests; every domain can be
exported and deleted; and the delegation parts stay inside their limits."""
import json
import os
import stat
from datetime import datetime
from pathlib import Path

import pytest

from minty_core.butler.domain import Butler, ButlerError
from minty_core.butler.domains import all_domains
from minty_core.butler.store import ButlerStore
from tests.plugin_helpers import make_host

SRC = Path(__file__).resolve().parents[1] / "src" / "minty_core"
NOW = datetime(2026, 10, 15, 10, 0).timestamp()
SAMPLE = {"chores": "Water the plants", "tasks": "Call the dentist", "waiting": "Ravi: the form",
          "place": "keys", "leaving": "umbrella", "documents": "passport", "ideas": "a gripper",
          "competitions": "Maths olympiad"}


@pytest.fixture
def butler(tmp_path):
    return Butler(ButlerStore(tmp_path / "butler.db", clock=lambda: NOW), all_domains(), clock=lambda: NOW)


def add(butler, name):
    return butler.add(name, SAMPLE[name], when="20 Nov" if name in ("waiting", "documents", "competitions") else "",
                      data={"where": "in the drawer"} if name == "place" else None)


@pytest.mark.parametrize("name", sorted(all_domains()))
def test_every_domain_can_be_exported_and_deleted(butler, name):
    add(butler, name)
    other = [n for n in all_domains() if n != name][0]
    add(butler, other)
    exported = json.loads(butler.export(name))
    assert exported and all(row["domain"] == name for row in exported)
    assert butler.delete(name, "all") >= 1
    assert json.loads(butler.export(name)) == []
    assert json.loads(butler.export(other))  # the neighbour is untouched


@pytest.mark.parametrize("name", sorted(all_domains()))
def test_no_domain_keeps_an_id_number(butler, name):
    with pytest.raises(ButlerError):
        butler.add(name, "renew 2345 6789 0124 soon", when="20 Nov")


@pytest.mark.parametrize("name", sorted(all_domains()))
async def test_every_domain_has_export_and_delete_through_the_tools(tmp_path, name):
    h = make_host(tmp_path / "cfg")
    data = h._store.config.model_dump(mode="json")
    data["butler"]["enabled"] = True
    h._store.update(data)
    args = {"domain": name, "name": SAMPLE[name]}
    if name in ("waiting", "documents", "competitions"):
        args["when"] = "20 Nov"
    assert (await h.call("butler_add", args)).startswith("Added")
    assert json.loads(await h.call("butler_export", {"domain": name}))
    assert "Deleted 1" in await h.call("butler_delete", {"domain": name, "item": "all"})


def test_butler_data_stays_in_one_owner_only_file(butler, tmp_path):
    add(butler, "chores")
    assert stat.S_IMODE(os.stat(tmp_path / "butler.db").st_mode) == 0o600


def test_the_framework_has_no_network_and_no_program_launching():
    banned = {"httpx", "requests", "urllib", "socket", "aiohttp", "smtplib", "subprocess", "webbrowser", "http"}
    import ast

    for path in sorted((SRC / "butler").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("system", "popen", "execv", "execvp", "spawnl",
                                                                  "startfile"):
                raise AssertionError(f"{path.name} launches a program via {node.attr}")
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                assert n.split(".")[0] not in banned, f"{path.name} imports {n}"


def test_a_new_domain_is_a_few_lines_of_data(butler):
    from minty_core.butler.domain import Domain

    plants = Domain("plants", "plant care", noun="plant", aliases=("plants", "plant care"), repeating=True,
                    remind="Water: {name}.")
    b = Butler(butler.store, {**butler.domains, "plants": plants}, clock=lambda: NOW)
    assert "Added fern" in b.capture("add fern to my plants every 3 days")
    assert "fern" in b.capture("what are my plants")
