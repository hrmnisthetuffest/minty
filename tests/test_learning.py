import pytest

from tests.plugin_helpers import make_host
from minty_core.webmemory import Macro, MacroStep, MacroStore, Proposals, QUARANTINE_AFTER, steps_from_trace

STEPS = [MacroStep("open", url="https://shop.example/checkout"), MacroStep("type", role="textbox", name="Name",
                                                                         text="{who}"),
         MacroStep("click", role="button", name="Save", risk=1)]


def test_macro_lifecycle_statuses(tmp_path):
    store = MacroStore(tmp_path / "m.json")
    store.save(Macro("form", STEPS, status="draft"))
    assert store.approve("form", "2026-10-12") == "it has not passed a test run yet: run it once first"
    store.record_run("form", True)
    assert store.get("form").tested_ok and store.get("form").status == "draft"
    assert store.approve("form", "2026-10-12") == ""
    m = store.get("form")
    assert (m.status, m.version, m.changelog) == ("active", 2, "approved by you on 2026-10-12")
    assert store.approve("form", "2026-10-12") == "it is already active"
    assert "3 steps on shop.example" in m.summary() and "takes who" in m.summary()


def test_three_failures_in_a_row_quarantine_and_a_success_resets(tmp_path):
    store = MacroStore(tmp_path / "m.json")
    store.save(Macro("form", STEPS))
    for _ in range(QUARANTINE_AFTER - 1):
        store.record_run("form", False)
    store.record_run("form", True)  # the streak is broken
    assert store.get("form").status == "active"
    for _ in range(QUARANTINE_AFTER):
        store.record_run("form", False)
    m = store.get("form")
    assert m.status == "quarantined" and "3 failures in a row" in m.changelog


def test_proposals_need_enough_steps_and_are_not_repeated(tmp_path):
    p = Proposals(tmp_path / "p.json")
    steps = steps_from_trace([{"tool": "open", "url": "https://a.example"}, {"tool": "click", "role": "button",
                                                                           "name": "Go"}])
    assert p.offer("do the thing", steps) is None  # two steps: not worth a routine
    steps = STEPS
    first = p.offer("add the lamp to the cart", steps)
    assert first == 1 and p.offer("add the lamp to the cart", steps, {"add the lamp to the cart"}) is None
    assert p.latest()["goal"] == "add the lamp to the cart"
    assert p.take(first)["id"] == 1 and p.take() is None


async def test_the_learning_plugin_walks_a_draft_to_active(tmp_path):
    host = make_host(tmp_path, plugins={"browser": {"enabled": False}},
                     services={"today": lambda: "2026-10-12"})
    Proposals(tmp_path / "proposals.json").offer("fill the delivery form", STEPS)
    assert (await host.call("learn_approve", {"name": "x"})).startswith("Error: no routine")
    out = await host.call("learn_remember", {"name": "delivery form"})
    assert out.startswith("Saved a draft routine called 'delivery-form': 3 steps on shop.example")
    assert (await host.call("learn_approve", {"name": "delivery form"})).startswith("Error: not approved, it has not")
    MacroStore(tmp_path / "macros.json").record_run("delivery form", True)  # the test run
    assert (await host.call("learn_approve", {"name": "delivery form"})).startswith("Approved.")
    assert "delivery-form (active, v2, 1 runs, 0 failed)" in await host.call("learn_list", {})
    assert await host.call("learn_review", {}) == "All routines are healthy."
    assert (await host.call("learn_remember", {})).startswith("There's nothing waiting")
    tool = host.get_tool("learn_approve")
    assert tool.risk == 2 and "shop.example" in tool.describe({"name": "delivery form"})


async def test_review_flags_weak_and_quarantined_without_changing_them(tmp_path):
    host = make_host(tmp_path, plugins={"browser": {"enabled": False}})
    store = MacroStore(tmp_path / "macros.json")
    store.save(Macro("flaky", STEPS))
    for ok in (True, False, False, True):
        store.record_run("flaky", ok)
    store.save(Macro("dead", STEPS))
    for _ in range(3):
        store.record_run("dead", False)
    out = await host.call("learn_review", {})
    assert "flaky: failed 2 of 4 runs" in out and "dead: quarantined" in out and "won't change them" in out
    assert store.get("flaky").status == "active"  # reported, not touched
