"""WP-22 acceptance: residency, imports, Hevy and consent, through the real agent, router and plugin host."""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from tests.plugin_helpers import make_host
from minty_core.agent import Agent
from minty_core.commands import CommandRouter
from minty_core.config import ConfigStore
from minty_core.health import HealthCommands, HealthHub
from minty_core.health_import import import_bytes
from minty_core.hevy import HevyPoller
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from tests.test_health_import import CRONOMETER, HEVY_CSV

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
PLANTED = "Sukhna Lake 10.4 km"  # only the Strava-origin record has this


class Recording:
    """Stands in for every provider; remembers who was sent what."""

    def __init__(self, log, name, script):
        self.log, self.name, self.script = log, name, script

    async def stream(self, messages, tools):
        self.log.append((self.name, "\n".join(f"{m.role}: {m.content}" for m in messages)))
        for ev in self.script.pop(0) if self.script else [TextDelta("Noted.")]:
            yield ev


def build(tmp_path, script):
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["toolkit_routing"] = False
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for cfg in store.config.providers:
        if cfg.requires_key:
            secrets.set(cfg.key_name or cfg.name, "key-" + cfg.name)
    hub = HealthHub(tmp_path / "health.db", clock=NOW.timestamp)
    for d in ("training", "body", "sleep", "nutrition", "activity"):
        hub.grant(d)
    seen: list[tuple[str, str]] = []
    router = ProviderRouter(store, secrets, lambda cfg, key: Recording(seen, cfg.name, script))
    host = make_host(tmp_path / "cfg", services={"health": hub})
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    async def confirm(prompt):
        return True

    agent = Agent(router, host, store, personalities, MemoryStore(tmp_path / "m.md"), confirm)
    return agent, hub, seen, store


def call(name, args=None):
    return [ToolCallEvent(ToolCall("1", name, args or {}))]


async def test_a_planted_strava_record_never_reaches_any_provider(tmp_path):
    script = [call("health_show", {"domain": "activity", "days": 30}),  # its result goes to you and ends the turn
              call("health_summary", {"days": 14}),
              [TextDelta("A solid fortnight.")]]
    agent, hub, seen, store = build(tmp_path, script)
    t = (NOW - timedelta(days=2)).timestamp()
    hub.add_activity("strava_api", "s1", t, "Run", 10400, 3133, 161, 40)  # LOCAL_ONLY, the planted record
    hub.add_activity("strava_export", "s2", t - 86400, "Ride", 20000, 3600, 130, 100)  # SENSITIVE: facts only
    hub.set_profile("age", "15")
    shown = "".join([s async for s in agent.respond("show me my recent activities")])
    assert "Run" in shown and "10.4 km" in shown  # you can see it
    summary = "".join([s async for s in agent.respond("how was my last fortnight")])
    assert summary == "A solid fortnight."
    assert seen, "the fake providers were never used"
    assert all("10.4" not in prompt and "Run" not in prompt.split("tool:")[-1] for _, prompt in seen)
    assert all("strava_api" not in prompt for _, prompt in seen)
    # the facts pack (derived, sensitive) was only ever sent to providers that allow private turns
    pack_calls = [name for name, prompt in seen if "Other activity" in prompt]
    allowed = {c.name for c in store.config.providers if c.effective_policy()["allow_sensitive"]}
    assert pack_calls and set(pack_calls) <= allowed


async def test_the_same_export_twice_adds_nothing_and_junk_is_refused(tmp_path):
    agent, hub, seen, _ = build(tmp_path, [])
    first = import_bytes(hub, HEVY_CSV.encode(), "w.csv")
    second = import_bytes(hub, HEVY_CSV.encode(), "w.csv")
    assert first.added == 2 and second.already_imported and hub.counts()["training"] == 2
    third = import_bytes(hub, CRONOMETER.encode(), "d.csv")
    again = import_bytes(hub, CRONOMETER.replace("2026-10-11", "2026-10-11 ").encode(), "d2.csv")
    assert third.added == 2 and again.duplicates >= 1


async def test_hevy_polling_is_read_only_and_the_key_stays_out_of_every_log(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    agent, hub, seen, _ = build(tmp_path, [])
    key = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"page": 1, "page_count": 1, "workouts": []})

    poller = HevyPoller(hub, lambda: key, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await poller.sync_once()
    assert requests and all(r.method == "GET" for r in requests)
    assert key not in caplog.text and all(key not in str(r.url) for r in requests)


async def test_stopping_consent_stops_collection_and_deletion_needs_two_steps(tmp_path):
    agent, hub, seen, _ = build(tmp_path, [])
    import_bytes(hub, HEVY_CSV.encode(), "w.csv")

    class Dummy:
        history: list = []

    router = CommandRouter(Dummy(), health_data=HealthCommands(hub))
    assert "stopped" in router.handle("stop collecting my health data").lower()
    report = import_bytes(hub, HEVY_CSV.replace("Legs", "Legs two").encode(), "w2.csv")
    assert report.added == 0 and report.skipped_consent.get("training") == 2
    assert hub.counts()["training"] == 2  # the old data is still there, until you say so
    assert "2" in router.handle("delete my workout data") and hub.counts()["training"] == 2
    assert "Deleted 2" in router.handle("yes delete my workout data") and hub.counts()["training"] == 0
