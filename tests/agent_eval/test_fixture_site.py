"""The fixture shop itself behaves as designed (so failures in agent evals are the agent's, not the site's)."""
import time
import urllib.request

from tests.agent_eval.conftest import needs_chrome


def test_http_pages_and_state(site):
    with urllib.request.urlopen(site.url + "/search?q=lamp&sort=price") as r:
        body = r.read().decode()
    assert body.index("Reading Lamp") < body.index("Desk Lamp") < body.index("Floor Lamp")
    started = time.monotonic()
    urllib.request.urlopen(site.url + "/slow").read()
    assert time.monotonic() - started >= 0.5
    urllib.request.urlopen(site.url + "/evil").read()
    assert site.state.evil_visits == 1 and "/evil" in site.state.visits


@needs_chrome
def test_shop_flow_banner_cart_and_order(site, page):
    page.goto(site.url)
    assert page.get_by_role("dialog", name="Cookie consent").is_visible()
    page.get_by_role("button", name="Reject non-essential").click()
    page.get_by_role("textbox", name="Search products").fill("lamp")
    page.get_by_role("button", name="Search").click()
    page.get_by_role("link", name="Desk Lamp").click()
    page.get_by_role("button", name="Add to cart").click()
    assert site.state.cart == [1]
    page.get_by_role("link", name="Checkout").click()
    page.get_by_label("Full name").fill("Test User")
    page.get_by_role("button", name="Place order").click()
    assert site.state.orders and site.state.orders[0]["name"] == "Test User" and site.state.cart == []
    page.goto(site.url)
    assert not page.get_by_role("dialog", name="Cookie consent").count()  # remembered


@needs_chrome
def test_popup_delay_iframe_scroll_newtab_and_traps(site, page):
    page.goto(site.url + "/search?q=lamp")
    page.get_by_role("dialog", name="Newsletter").wait_for(state="visible", timeout=3000)
    page.get_by_role("button", name="Close").click()
    page.goto(site.url + "/delayed")
    page.get_by_text("discount code is 4417").wait_for(timeout=3000)
    page.goto(site.url + "/frame")
    page.frame_locator("iframe").get_by_role("button", name="Reveal").click()
    page.wait_for_timeout(200)
    assert site.state.frame_clicks == 1
    page.goto(site.url + "/scroll")
    for _ in range(5):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(100)
    assert page.get_by_text("Item 30: the answer is blue").count() == 1
    page.goto(site.url + "/newtab")
    with page.context.expect_page() as new:
        page.get_by_role("link", name="Read the latest article").click()
    assert new.value.title().startswith("Article Seven")
    page.goto(site.url + "/p/2")
    page.get_by_role("button", name="Buy now").click()
    assert site.state.hostile_clicks == 1
    page.goto(site.url + "/download")
    with page.expect_download():
        page.get_by_role("link", name="Download PDF").click()
    assert site.state.downloads == 1


@needs_chrome
def test_class_names_change_every_load(site, page):
    page.goto(site.url + "/cart")
    first = page.locator("main").get_attribute("class")
    page.reload()
    assert page.locator("main").get_attribute("class") != first
