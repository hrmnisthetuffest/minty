"""Page helpers that read what the browser shows: what can be clicked, and the login hand-over."""
from minty_core.webpage import Node
from minty_core.web.steps import HandOver, asks_for_login, clickable_summary


def n(role, name, ref, **kw):
    return Node(role=role, name=name, ref=ref, **kw)


PAGE = [n("heading", "Boat Earbuds", ""), n("button", "Add to cart", "e1"), n("button", "Buy now", "e2"),
        n("button", "Wishlist", "e3"), n("link", "Home", "e4", href="/"), n("link", "Deals", "e5", href="/d"),
        n("textbox", "Search", "e6"), n("searchbox", "Find products", "e7"), n("combobox", "Sort by", "e8"),
        n("checkbox", "In stock only", "e9")]


def test_the_summary_groups_by_kind_counts_them_and_flags_the_risky_ones():
    text = clickable_summary(PAGE, "Boat Earbuds", "https://shop.example/p", risky=lambda x: x.label == "Buy now")
    assert text.startswith("Boat Earbuds.")
    assert "3 buttons: Add to cart, Buy now, Wishlist" in text and "2 links: Home, Deals" in text
    assert "2 text fields: Search, Find products" in text and "1 dropdown: Sort by" in text
    assert "1 tick box: In stock only" in text
    assert "Careful with: Buy now." in text


def test_a_popup_comes_first_and_long_lists_are_cut():
    many = [n("link", f"Link {i}", f"e{i}") for i in range(30)]
    pop = n("button", "Reject all", "e90", in_dialog=True, dialog="Cookies")
    text = clickable_summary([*many, pop], "Shop", "https://x.example/")
    assert text.index("In a pop-up") < text.index("30 links") and "Reject all" in text
    assert "and 25 more" in text


def test_a_page_with_nothing_to_click():
    assert clickable_summary([n("paragraph", "", "")], "Plain", "https://x.example/") == \
        "Plain. I don't see anything to click on this page."


def test_login_detection_looks_for_a_password_box_not_the_word_login():
    assert asks_for_login([n("textbox", "Password", "e1")])
    assert asks_for_login([n("textbox", "Enter the one-time code", "e1")])
    assert not asks_for_login([n("link", "Login", "e1"), n("textbox", "Search", "e2")])


def test_hand_over_waits_while_you_log_in_and_never_asks_for_the_password():
    t = [100.0]
    h = HandOver(clock=lambda: t[0], patience_s=600)
    login = [n("textbox", "Email", "e1"), n("textbox", "Password", "e2")]
    msg = h.start("https://bank.example/login", login)
    assert "I will not type" in msg and "yourself" in msg
    done, text = h.check("https://bank.example/login", login)
    assert not done and "waiting" in text.lower()
    t[0] += 30
    done, text = h.check("https://bank.example/home", [n("heading", "Welcome back", "")])
    assert done and "carry on" in text.lower() and not h.active


def test_hand_over_gives_up_after_its_patience_and_needs_a_login_page_to_start():
    t = [0.0]
    h = HandOver(clock=lambda: t[0], patience_s=300)
    import pytest

    with pytest.raises(ValueError, match="no login"):
        h.start("https://x.example/", [n("button", "Buy", "e1")])
    h.start("https://x.example/login", [n("textbox", "Password", "e1")])
    t[0] += 301
    done, text = h.check("https://x.example/login", [n("textbox", "Password", "e1")])
    assert done and "stopped waiting" in text and not h.active
    assert h.check("https://x.example/", [])[1] == "No hand-over is waiting."
