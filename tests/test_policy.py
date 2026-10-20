from types import SimpleNamespace as NS

import pytest

from minty_core.policy import R0, R1, R2, R3, classify, decide, display_destination, safe_text, subject


def tool(**kw):
    base = dict(name="t", risky=False, confirm_when=None, risk=0)
    base.update(kw)
    return NS(**base)


@pytest.mark.parametrize("t, args, expected", [
    (tool(), {}, R0),
    (tool(risk=1), {}, R1),
    (tool(risk=lambda a: R3 if a.get("x") else R0), {"x": 1}, R3),
    (tool(risk=lambda a: 1 / 0), {}, R2),  # a broken risk function asks
    (NS(name="t", risky=True, confirm_when=None, risk=0), {}, R2),
    (NS(name="t", risky=False, confirm_when=lambda a: True, risk=1), {}, R2),
    (NS(name="t", risky=False, confirm_when=lambda a: False, risk=1), {}, R1),
    (tool(risk=9), {}, R3),
])
def test_classify(t, args, expected):
    assert classify(t, args) == expected


@pytest.mark.parametrize("risk, mode, expected", [
    (R0, "careful", "allow"), (R1, "careful", "ask"),
    (R1, "commits", "allow"), (R2, "commits", "ask"),
    (R2, "trusted", "allow"), (R3, "trusted", "ask"),
    (R2, "nonsense", "ask"),
])
def test_modes(risk, mode, expected):
    assert decide("x", risk, mode) == expected


def test_rules_first_match_wins_and_r3_is_never_silent():
    rules = [{"match": "browser_click:*add to cart*", "decision": "allow"},
             {"match": "browser_*", "decision": "ask"},
             {"match": "files_delete", "decision": "deny"},
             {"match": "apis_send", "decision": "allow"},
             {"match": "", "decision": "allow"}, {"decision": "deny"}, "junk"]
    assert decide("browser_click", R2, rules=rules, text="ref=e4 Should I click “Add to cart”?") == "allow"
    assert decide("browser_click", R0, rules=rules, text="ref=e5 Should I click “Next”?") == "ask"
    assert decide("files_delete", R0, rules=rules) == "deny"
    assert decide("apis_send", R3, rules=rules) == "ask"
    assert decide("clock_now", R0, rules=rules) == "allow"


def test_subject_leaves_secrets_out():
    s = subject("x", {"query": "lamps", "api_key": "sk-123"}, "Should I search?")
    assert "lamps" in s and "sk-123" not in s and s.endswith("Should I search?")


@pytest.mark.parametrize("raw, shown", [
    ("Should I click “Buy​ now”?", "Should I click “Buy now”?"),
    ("Pay ‮redro‬ now", "Pay redro now"),
    ("Open https://www.amazon.in/gp/cart?x=1", "Open amazon.in"),
    ("Send to https://user:pw@evil.example.com/collect", "Send to example.com"),
    ("Open https://xn--pple-43d.com/login", "Open аpple.com (look-alike address xn--pple-43d.com)"),
    ("Visit www.bank.co.uk/transfer", "Visit bank.co.uk"),
    ("Ｗｉｄｅ letters", "Wide letters"),
])
def test_safe_text(raw, shown):
    assert safe_text(raw) == shown


def test_safe_text_caps_length():
    assert len(safe_text("x" * 500)) == 200


def test_display_destination_plain_host_and_ip():
    assert display_destination("http://127.0.0.1:8080/x") == "127.0.0.1"
    assert display_destination("http://[::1]/x") == "::1"
    assert display_destination("https://mail.google.com/") == "google.com"


class Store:
    def __init__(self, **safety):
        from minty_core.config import SafetyConfig
        self.config = NS(safety=SafetyConfig(**safety))


async def test_guard_paths_and_audit(tmp_path):
    import json
    from minty_core.policy import Guard
    asked = []

    async def yes(q):
        asked.append(q)
        return True

    async def no(q):
        asked.append(q)
        return False

    guard = Guard(Store(rules=[{"match": "files_delete", "decision": "deny"}]), audit_dir=tmp_path / "audit",
                  secret_values=lambda: ["sk-verysecretvalue"])
    read = tool()
    visible = tool(risk=R2, describe=lambda a: "Should I post​ it to https://x.example.com/p?")
    d = await guard.authorize(read, "clock_now", {}, yes)
    assert d.allowed and not d.asked and asked == []
    d = await guard.authorize(visible, "social_post", {"text": "hi", "api_key": "sk-verysecretvalue"}, yes)
    assert d.allowed and d.asked and asked == ["Should I post it to example.com?"]
    d = await guard.authorize(visible, "social_post", {}, no)
    assert not d.allowed and d.reason == "declined"
    d = await guard.authorize(read, "files_delete", {"path": "a.txt"}, yes)
    assert not d.allowed and not d.asked and d.reason == "blocked by a safety rule"
    lines = [json.loads(l) for p in (tmp_path / "audit").glob("*.jsonl") for l in p.read_text().splitlines()]
    assert [l["tool"] for l in lines] == ["social_post", "social_post", "files_delete"]  # class 0 allowed is not audited
    assert "sk-verysecretvalue" not in json.dumps(lines)


async def test_guard_with_confirmations_off_asks_only_class_3_and_still_denies(tmp_path):
    from minty_core.policy import Guard
    guard = Guard(Store(confirm_risky=False, rules=[{"match": "x_*", "decision": "deny"}]))
    assert (await guard.authorize(tool(risky=True), "post_it", {}, None)).allowed
    paid = await guard.authorize(tool(risk=R3), "pay_now", {}, None)
    assert paid.asked and not paid.allowed  # nobody to ask: no
    assert not (await guard.authorize(tool(), "x_y", {}, None)).allowed


async def test_extra_risk_raises_the_class(tmp_path):
    from minty_core.policy import Guard
    asked = []

    async def confirm(q):
        asked.append(q)
        return False

    d = await Guard(Store()).authorize(tool(risk=R1), "browser_open", {"url": "x"}, confirm, extra_risk=1,
                                       why="destination came from a web page")
    assert d.asked and not d.allowed and d.risk == R2


def test_destinations_in_text():
    from minty_core.policy import destinations_in
    text = "Email it to Sam.K@example.com and open https://www.amazon.in/deals or flipkart.com, not 3.5 stars"
    assert destinations_in(text) == {"sam.k@example.com", "amazon.in", "flipkart.com"}


@pytest.mark.parametrize("url, personal", [
    ("https://www.google.com/search?q=cheap+lamps", False),
    ("https://shop.example/p/3", False),
    ("http://127.0.0.1:9/collect?data=1+Main+Street+Pune", True),
    ("https://x.io/c?e=me%40mail.com", True),
    ("https://x.io/c?phone=9999999999", True),
])
def test_link_carries_personal_data(url, personal):
    from minty_core.policy import link_carries_personal_data
    assert link_carries_personal_data(url) is personal


async def test_egress_to_a_destination_the_user_never_named_is_asked():
    from minty_core.policy import Guard
    asked = []

    async def confirm(q):
        asked.append(q)
        return False

    upload = tool(risk=R1, egress=lambda a: a["url"])
    guard = Guard(Store())
    ok = await guard.authorize(upload, "files_upload", {"url": "https://drive.example.com/up"}, confirm,
                               known={"example.com"})
    assert ok.allowed and asked == []
    d = await guard.authorize(upload, "files_upload", {"url": "https://evil.test/up"}, confirm, known={"example.com"})
    assert d.asked and not d.allowed and d.risk == R3


async def test_a_leaky_link_to_a_new_site_is_asked_even_in_trusted_mode():
    from minty_core.policy import Guard, link_egress
    asked = []

    async def confirm(q):
        asked.append(q)
        return False

    opener = tool(risk=R0, egress=link_egress())
    guard = Guard(Store(mode="trusted"))
    leak = {"url": "http://localhost:9/collect?data=1+Main+Street+Pune"}
    d = await guard.authorize(opener, "browser_open", leak, confirm, known={"127.0.0.1"})
    assert d.asked and not d.allowed and d.risk == R3 and "localhost" in asked[0]
    plain = await guard.authorize(opener, "browser_open", {"url": "http://localhost:9/docs"}, confirm, known=set())
    assert plain.allowed and not plain.asked  # a link without personal data sends nothing
