"""Cooldowns, strikes and demotions survive a restart (W0-15)."""
import json

from tests.test_router import Clock
from tests.test_router_health import OK, Queue, fail
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import Message


class Wall:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def make(tmp_path, scripts, clock, wall, persist=True):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [{"name": n, "model": "m"} for n in scripts]
    data["router"]["persist_state"] = persist
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for n in scripts:
        secrets.set(n, "key-" + n)
    calls = []

    def factory(cfg, key):
        calls.append(cfg.name)
        return Queue(cfg.name, scripts[cfg.name])

    router = ProviderRouter(store, secrets, factory, clock, state_path=tmp_path / "router-state.json", wall=wall)
    return router, calls


async def ask(router):
    return [e async for e in router.stream([Message("user", "hi")], [])]


async def test_a_resting_provider_is_still_resting_after_a_restart(tmp_path):
    wall = Wall()
    router, _ = make(tmp_path, {"a": [fail(429)], "b": [OK]}, Clock(), wall)
    await ask(router)
    saved = json.loads((tmp_path / "router-state.json").read_text())
    assert saved["cooldown"]["a"] == wall.now + 60 and saved["strikes"]["a"] == 1
    wall.now += 20  # restart 20 s later, with a fresh monotonic clock
    clock = Clock()
    clock.now = 5.0
    again, calls = make(tmp_path, {"a": [OK], "b": [OK]}, clock, wall)
    await ask(again)
    assert calls == ["b"]
    row = next(r for r in again.status() if r["name"] == "a")
    assert 39 <= row["cooling_for"] <= 40


async def test_strikes_carry_over_so_the_next_rest_is_longer(tmp_path):
    wall = Wall()
    router, _ = make(tmp_path, {"a": [fail(429)], "b": [OK]}, Clock(), wall)
    await ask(router)
    wall.now += 100  # the first rest is over
    clock = Clock()
    again, _ = make(tmp_path, {"a": [fail(429)], "b": [OK]}, clock, wall)
    await ask(again)
    assert next(r for r in again.status() if r["name"] == "a")["cooling_for"] == 120.0


async def test_expired_entries_are_dropped(tmp_path):
    wall = Wall()
    router, _ = make(tmp_path, {"a": [fail(500)], "b": [OK]}, Clock(), wall)
    await ask(router)
    wall.now += 3600
    again, calls = make(tmp_path, {"a": [OK], "b": [OK]}, Clock(), wall)
    await ask(again)
    assert calls == ["a"]


async def test_success_clears_the_saved_strikes(tmp_path):
    wall = Wall()
    clock = Clock()
    router, _ = make(tmp_path, {"a": [fail(500), OK], "b": [OK]}, clock, wall)
    await ask(router)
    clock.now += 100
    wall.now += 100
    await ask(router)
    saved = json.loads((tmp_path / "router-state.json").read_text())
    assert "a" not in saved["strikes"]


def test_a_damaged_state_file_is_ignored(tmp_path):
    (tmp_path / "router-state.json").write_text("{not json")
    router, _ = make(tmp_path, {"a": [OK]}, Clock(), Wall())
    assert router.status()[0]["cooling_for"] == 0.0


async def test_switched_off_nothing_is_written(tmp_path):
    router, _ = make(tmp_path, {"a": [fail(429)], "b": [OK]}, Clock(), Wall(), persist=False)
    await ask(router)
    assert not (tmp_path / "router-state.json").exists()


async def test_state_file_is_private(tmp_path):
    router, _ = make(tmp_path, {"a": [fail(429)], "b": [OK]}, Clock(), Wall())
    await ask(router)
    assert (tmp_path / "router-state.json").stat().st_mode & 0o777 == 0o600
