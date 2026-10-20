"""Known-vulnerability check shown inside the install question (W1-17)."""
import json

import httpx

from tests.safety_helpers import build, call, say
from tests.plugin_helpers import mock_http  # noqa: F401
from minty_core.depaudit import audit, note, parse_spec
from minty_core.safety import SafetyKit
from minty_core.secretscan import SecretScanner


def test_package_specs_are_parsed_for_each_manager():
    assert parse_spec("pip", "requests==2.31.0") == ("PyPI", "requests", "2.31.0")
    assert parse_spec("pip", "numpy>=1.20") == ("PyPI", "numpy", None)
    assert parse_spec("pipx", "black[d]") == ("PyPI", "black", None)
    assert parse_spec("npm", "left-pad@1.3.0") == ("npm", "left-pad", "1.3.0")
    assert parse_spec("npm", "@scope/pkg") == ("npm", "@scope/pkg", None)


def handler(vulns, pypi_status=200, osv_status=200):
    def fn(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pypi.org":
            return httpx.Response(pypi_status, json={"info": {"version": "9.9.9"}})
        if request.url.host == "api.osv.dev":
            body = json.loads(request.content)
            assert body["package"] == {"name": "evilpkg", "ecosystem": "PyPI"} and body["version"] == "9.9.9"
            return httpx.Response(osv_status, json={"vulns": vulns})
        return httpx.Response(404)
    return fn


async def test_a_vulnerable_package_is_flagged_with_ids(monkeypatch):
    mock_http(monkeypatch, handler([{"id": "GHSA-aaaa-bbbb", "summary": "Remote code execution"},
                                    {"id": "PYSEC-2024-1", "summary": "x"}]))
    a = await audit("pip", "evilpkg")
    assert a.checked and a.version == "9.9.9" and len(a.vulns) == 2
    text = note(a)
    assert "lists 2 known vulnerabilities for evilpkg 9.9.9 (GHSA-aaaa-bbbb, PYSEC-2024-1)" in text
    assert "Remote code execution" in text


async def test_a_clean_package_says_so_and_an_outage_says_it_could_not_check(monkeypatch):
    mock_http(monkeypatch, handler([]))
    assert note(await audit("pip", "evilpkg")) == "OSV.dev lists no known vulnerabilities for evilpkg 9.9.9."
    mock_http(monkeypatch, handler([], osv_status=503))
    out = note(await audit("pip", "evilpkg"))
    assert out.startswith("I couldn't check evilpkg 9.9.9") and "503" in out

    def down(request):
        raise httpx.ConnectError("no network")
    mock_http(monkeypatch, down)
    assert "couldn't check evilpkg" in note(await audit("pip", "evilpkg"))


async def test_the_note_is_part_of_the_install_question(tmp_path, monkeypatch):
    mock_http(monkeypatch, handler([{"id": "GHSA-aaaa-bbbb", "summary": "Remote code execution"}]))
    agent, host, asked = build(tmp_path, {"installer_install": (3, None)},
                               [[call("installer_install", {"manager": "pip", "package": "evilpkg"})]],
                               confirm_answer=False, safety=lambda store: SafetyKit(store, SecretScanner()))
    await say(agent, "install evilpkg")
    assert asked and "OSV.dev lists 1 known vulnerability for evilpkg 9.9.9" in asked[0] and host.called == []


async def test_it_can_be_switched_off(tmp_path, monkeypatch):
    mock_http(monkeypatch, handler([]))
    agent, host, asked = build(tmp_path, {"installer_install": (3, None)},
                               [[call("installer_install", {"manager": "pip", "package": "evilpkg"})]],
                               settings={"safety": {"audit_installs": False}},
                               safety=lambda store: SafetyKit(store, SecretScanner()))
    await say(agent, "install evilpkg")
    assert asked and "OSV" not in asked[0]
