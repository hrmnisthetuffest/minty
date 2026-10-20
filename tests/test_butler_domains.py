"""The other four domains and the butler plugin: follow-ups, where things are, the leaving checklist, renewals."""
import json
from datetime import datetime, timedelta

import pytest

from minty_core.butler.domain import Butler, ButlerError
from minty_core.butler.domains import all_domains
from minty_core.butler.store import ButlerStore
from tests.plugin_helpers import make_host

NOW = datetime(2026, 10, 15, 10, 0)


class Clock:
    def __init__(self):
        self.t = NOW.timestamp()

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw).total_seconds()


@pytest.fixture
def butler(tmp_path):
    clock = Clock()
    return Butler(ButlerStore(tmp_path / "b.db", clock=clock), all_domains(), clock=clock)


def test_every_domain_name_is_unique_and_documented():
    d = all_domains()
    assert set(d) == {"chores", "tasks", "waiting", "place", "leaving", "documents", "ideas", "competitions"}
    assert all(x.label and x.noun for x in d.values())


# ---- 1053 waiting-for --------------------------------------------------------------------------------------
def test_waiting_for_someone_gets_a_follow_up_date_and_comes_due(butler):
    out = butler.capture("I'm waiting for Ravi to send the form by monday")
    assert "Ravi: send the form" in out and "monday" in out.lower()
    assert "Ravi" in butler.capture("what am I waiting for")
    butler._clock.advance(days=5)
    (n,) = butler.notices(butler._clock())
    assert n.text == "Follow up: Ravi: send the form."


def test_waiting_without_a_date_defaults_to_three_days(butler):
    out = butler.capture("I'm waiting on Meera to reply about the trip")
    assert "Meera: reply about the trip" in out
    item = butler.list("waiting")[0]
    assert item.due == pytest.approx(butler._clock() + 3 * 86400, abs=3600 * 24)


def test_a_reply_closes_the_follow_up(butler):
    butler.capture("I'm waiting for Ravi to send the form")
    assert "Ticked off" in butler.capture("Ravi replied")
    assert butler.list("waiting") == []


# ---- 938 where did I put it --------------------------------------------------------------------------------
def test_remembers_where_things_are_and_answers_later(butler):
    assert "keys" in butler.capture("I put my keys in the blue bowl")
    assert butler.capture("where are my keys") == "keys: in the blue bowl."
    butler.capture("I left my keys on the desk")  # same thing again: updated, not duplicated
    assert butler.capture("where are my keys") == "keys: on the desk."
    assert len(butler.list("place")) == 1
    assert butler.capture("where is my umbrella") is None  # unknown: not answered by this domain


# ---- 937 leaving-home checklist ----------------------------------------------------------------------------
def test_leaving_home_reads_the_checklist_only_when_asked(butler):
    out = butler.capture("I'm leaving")
    assert out.startswith("Before you go:") and "keys" in out and "wallet" in out
    assert butler.notices(butler._clock()) == []  # never proactive
    assert butler.capture("I finished the keys") is None  # a checklist item is not ticked off for good


def test_the_checklist_can_be_changed(butler):
    butler.capture("add umbrella to my leaving checklist")
    assert "umbrella" in butler.capture("what's on my leaving checklist")


# ---- 822 documents with renewals ---------------------------------------------------------------------------
def test_a_document_renewal_reminds_early_and_on_the_day(butler):
    out = butler.capture("my passport expires on 20 Nov")
    assert "passport" in out.lower() and "20 Nov" in out
    assert butler.notices(butler._clock()) == []  # 36 days away: nothing yet
    butler._clock.advance(days=10)  # 26 days left: the 30-day lead is reached
    (n,) = butler.notices(butler._clock())
    assert "passport" in n.text.lower() and "26 days" in n.text and n.lead == 30
    butler.mark(n)
    assert butler.notices(butler._clock()) == []
    butler._clock.advance(days=20)  # 6 days left: the 7-day lead
    (n,) = butler.notices(butler._clock())
    assert n.lead == 7 and "6 days" in n.text


def test_documents_refuse_numbers(butler):
    with pytest.raises(ButlerError):
        butler.add("documents", "passport N1234567", when="20 Nov")
    assert "ID or card" in butler.capture("my passport K1234567 expires on 20 Nov")


# ---- the plugin --------------------------------------------------------------------------------------------
def host(tmp_path, enabled=True):
    h = make_host(tmp_path / "cfg")
    data = h._store.config.model_dump(mode="json")
    data["butler"]["enabled"] = enabled
    h._store.update(data)
    return h


async def test_plugin_is_off_until_switched_on(tmp_path):
    h = host(tmp_path, enabled=False)
    assert "switched off" in await h.call("butler_add", {"domain": "chores", "name": "plants"})


async def test_plugin_add_list_done_find_export_delete(tmp_path):
    h = host(tmp_path)
    assert (await h.call("butler_add", {"domain": "chores", "name": "Water the plants", "when": "every 3 days"})
            ).startswith("Added")
    assert "Water the plants" in await h.call("butler_list", {"domain": "chores"})
    await h.call("butler_add", {"domain": "place", "name": "passport", "where": "in the grey folder"})
    assert "grey folder" in await h.call("butler_find", {"domain": "place", "text": "passport"})
    assert "Ticked off" in await h.call("butler_done", {"domain": "chores", "item": "plants"})
    exported = json.loads(await h.call("butler_export", {"domain": "chores"}))
    assert [e["name"] for e in exported] == ["Water the plants"]
    assert "Deleted 1" in await h.call("butler_delete", {"domain": "chores", "item": "all"})
    assert "empty" in await h.call("butler_list", {"domain": "chores"})


async def test_plugin_risk_classes(tmp_path):
    h = host(tmp_path)
    assert h.get_tool("butler_add").risk == 1 and h.get_tool("butler_done").risk == 1
    delete = h.get_tool("butler_delete").risk
    assert delete({"item": "all"}) == 3 and delete({"item": "plants"}) == 2
    assert h.get_tool("butler_list").risk == 0 and h.get_tool("butler_find").risk == 0


def test_the_provider_delivers_due_notices_once_and_only_when_on(tmp_path):
    from minty_core.butler.kit import ButlerProvider
    from minty_core.config import Config

    cfg, clock = Config(), Clock()
    provider = ButlerProvider(config=lambda: cfg, directory=tmp_path, clock=clock)
    said: list[str] = []
    assert provider.deliver(said.append) == 0 and not (tmp_path / "butler.db").exists()
    cfg.butler.enabled = True
    provider.butler().add("chores", "Plants", when="in 1 hours")
    clock.advance(hours=2)
    assert provider.deliver(said.append) == 1 and said == ["Chore time: Plants."]
    assert provider.deliver(said.append) == 0  # not again
    cfg.butler.reminders = False
    clock.advance(days=2)
    assert provider.deliver(said.append) == 0


def test_the_voice_router_hands_butler_phrases_to_the_butler(tmp_path):
    from minty_core.butler.kit import ButlerProvider
    from minty_core.commands import CommandRouter
    from minty_core.config import Config

    cfg = Config()
    cfg.butler.enabled = True
    provider = ButlerProvider(config=lambda: cfg, directory=tmp_path, clock=Clock())
    router = CommandRouter(None, None, butler=provider.capture)
    assert "Added water the plants" in router.handle("add water the plants to my chores")
    assert router.handle("what is the weather like") is None
