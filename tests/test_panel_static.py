import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from minty_core._toml import tomllib

STATIC = Path(__file__).parent.parent / "src" / "minty_panel" / "static"
JS_FILES = sorted(STATIC.rglob("*.js"))
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def test_entry_files_exist():
    for name in ("index.html", "app.css", "app.js", "lib.js"):
        assert (STATIC / name).is_file(), name
    html = (STATIC / "index.html").read_text()
    assert '<script type="module" src="/static/app.js">' in html
    assert 'href="/static/app.css"' in html and 'id="app"' in html and 'id="toasts"' in html


def test_the_design_credit_is_on_every_screen():
    html = (STATIC / "index.html").read_text()
    assert "Design inspired by" in html and 'href="https://nousresearch.com"' in html and "Nous Research" in html
    assert 'rel="noopener noreferrer"' in html  # an external link leaks no referrer or window handle


def test_the_search_bar_and_settings_page_are_wired_in():
    app = (STATIC / "app.js").read_text()
    assert 'from "./palette.js"' in app and 'from "./pages/settings.js"' in app and "settings-schema.js" in app
    assert "audio: \"settings\"" in app  # the old address of the voice page still works
    assert not (STATIC / "pages" / "audio.js").exists()
    worker = (STATIC / "sw.js").read_text()
    assert "/static/palette.js" in worker and "/static/settings-schema.js" in worker


def test_every_relative_import_resolves():
    for js in JS_FILES:
        for target in re.findall(r'from\s+"(\.{1,2}/[^"]+)"', js.read_text()):
            assert (js.parent / target).resolve().is_file(), f"{js.name} imports missing {target}"


FORBIDDEN = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(")


def test_no_markup_injection_apis_anywhere():
    for js in JS_FILES:
        text = js.read_text()
        for token in FORBIDDEN:
            assert token not in text, f"{js.name} uses {token}"


def test_login_and_logout_send_the_csrf_header():
    app = (STATIC / "app.js").read_text()
    for path in ("/login", "/logout"):
        call = re.search(r'fetch\("%s",\s*\{.*?\}\s*\)' % path, app, re.S)
        assert call, f"no fetch of {path}"
        assert "X-Requested-With" in call.group(0), path


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("js", JS_FILES, ids=lambda p: str(p.relative_to(STATIC)))
def test_javascript_parses(js, tmp_path):
    copy = tmp_path / (js.stem + ".mjs")
    copy.write_text(js.read_text())
    result = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_events_runner_and_api_behaviour(tmp_path):
    copy = tmp_path / "static"
    shutil.copytree(STATIC, copy)
    (copy / "package.json").write_text('{"type": "module"}')
    script = Path(__file__).parent / "panel_js_checks.mjs"
    result = subprocess.run(
        ["node", "--test", str(script)],
        capture_output=True, text=True, env={**os.environ, "PANEL_STATIC": str(copy)},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_package_data_ships_nested_static_files():
    # Pages live in static/pages/; "static/*" alone would leave them out of a built wheel.
    data = tomllib.loads(PYPROJECT.read_text())
    globs = data["tool"]["setuptools"]["package-data"]["minty_panel"]
    assert "static/*" in globs
    assert "static/pages/*" in globs
