"""A page reading stays inside a character budget (speed fix S-1): the live trace had readings up to 10 K characters."""
import pytest

from tests.test_browser_recovery import FakePage, world  # noqa: F401 — fixture
from tests.test_plugin_browser import browser_host
from minty_core.webpage import PAGE_TAG, parse_aria, render_page


def big_page(dialog_buttons=3, buttons=120, words=60):
    snap = "- dialog \"Sign in to continue\" [ref=d0]:\n"
    snap += "".join(f'  - button "Dialog choice number {i}" [ref=d{i + 1}]\n' for i in range(dialog_buttons))
    snap += "- main [ref=m0]:\n"
    snap += "".join(f'  - link "Product {i} with a rather long descriptive name" [ref=e{i}]\n  - /url: /p/{i}\n'
                    for i in range(buttons))
    snap += "".join(f"  - paragraph [ref=p{i}]: {'lorem ipsum dolor ' * words}\n" for i in range(12))
    return parse_aria(snap)


def test_budget_keeps_a_reading_inside_its_size_and_the_most_useful_parts():
    nodes = big_page()
    old, _ = render_page(nodes, "Shop", "https://shop.example")
    new, items = render_page(nodes, "Shop", "https://shop.example", budget=3500)
    assert len(old) > 7000 and len(new) <= 3500
    assert new.startswith(PAGE_TAG) and "Page: 'Shop' https://shop.example" in new
    for i in range(3):  # a dialog blocks the page: all its controls stay
        assert f"Dialog choice number {i}" in new
    assert "Product 15 " in new and "Product 119 " not in new  # the first screenful stays, the tail goes
    assert "more elements below; scroll, or use browser_read" in new
    assert "Text: " in new and "browser_read" in new.splitlines()[-1]  # some words kept, and how to get the rest
    assert "d1" in items and "e119" in items  # every element is still addressable by ref


def test_budget_zero_or_none_is_the_old_reading():
    nodes = big_page()
    old, _ = render_page(nodes, "Shop", "https://shop.example")
    assert render_page(nodes, "Shop", "https://shop.example", budget=0)[0] == old
    assert render_page(nodes, "Shop", "https://shop.example", budget=None)[0] == old


def test_a_small_page_is_untouched_by_the_budget():
    snap = parse_aria('- main [ref=m]:\n  - button "Go" [ref=e1]\n  - paragraph [ref=p]: hello there\n')
    assert render_page(snap, "t", "u", budget=3500)[0] == render_page(snap, "t", "u")[0]


BODY = "\n".join(
    ["- main [ref=m0]:"] + [f'  - link "Product {i} with a rather long descriptive name" [ref=e{i}]' for i in range(150)]
    + [f"  - paragraph [ref=p{i}]: {'lorem ipsum dolor ' * 60}" for i in range(12)])


@pytest.fixture
def reads_a_big_page(monkeypatch):
    class Body:
        async def aria_snapshot(self, **kw):
            return BODY

    monkeypatch.setattr(FakePage, "locator", lambda self, *a, **kw: Body())


async def test_the_browser_reads_inside_the_budget_and_the_setting_restores_the_old_reading(
        tmp_path, world, reads_a_big_page):
    lean = await browser_host(tmp_path / "a").call("browser_open", {"url": "https://shop.example"})
    old = await browser_host(tmp_path / "b", page_budget=0).call("browser_open", {"url": "https://shop.example"})
    assert len(lean) <= 3500 < len(old)
    assert "[e0]" in lean and "more elements below" in lean
