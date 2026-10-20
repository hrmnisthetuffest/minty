import shutil

import pytest

from tests.agent_eval.fixture_site import FixtureSite

HAS_CHROME = any(shutil.which(b) for b in ("google-chrome-stable", "google-chrome", "chromium"))
needs_chrome = pytest.mark.skipif(not HAS_CHROME, reason="no Chrome available")


@pytest.fixture
def site():
    with FixtureSite(slow_s=0.5) as s:
        yield s


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()
        yield context.new_page()
        browser.close()
