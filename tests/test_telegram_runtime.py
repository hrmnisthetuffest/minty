"""Telegram wired into the app: built from settings and the key store, lockdown closes it, pairing by terminal (W3-08)."""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from tests.telegram_fake import TOKEN, FakeTelegram
from minty_core.approvals import ApprovalStore
from minty_core.config import ConfigStore
from minty_core.events import EventBus
from minty_core.keystore import SecretsStore
from minty_core.lockdown import Lockdown
from minty_core.telegram.runtime import build_runtime, pairing_instructions

OWNER = 1111


class Agent:
    history: list = []

    async def respond(self, text, **kw):
        yield "Fine, thanks."


def make_ctx(tmp_path, *, enabled=True, token=True, privacy=False):
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["telegram"].update(enabled=enabled, allowed_ids=[OWNER], machine_name="laptop")
    data["safety"]["privacy_mode"] = privacy
    store.update(data)
    secrets = SecretsStore(tmp_path)
    if token:
        secrets.set("telegram", TOKEN)

    async def call(name, args):
        return "ok"

    services = {"approvals": ApprovalStore(tmp_path / "approvals.sqlite"), "lockdown": Lockdown(tmp_path / "lock.json"),
                "is_private": lambda: False}
    return SimpleNamespace(config_dir=tmp_path, store=store, secrets=secrets, agent=Agent(), bus=EventBus(),
                           host=SimpleNamespace(call=call), services=services)


async def until(check, seconds=3.0):
    for _ in range(int(seconds / 0.01)):
        if check():
            return True
        await asyncio.sleep(0.01)
    return False


def test_nothing_is_built_when_it_is_off_or_there_is_no_token(tmp_path):
    assert build_runtime(make_ctx(tmp_path / "a", enabled=False), client=FakeTelegram().client) is None
    assert build_runtime(make_ctx(tmp_path / "b", token=False), client=FakeTelegram().client) is None


async def test_it_answers_the_paired_chat_and_publishes_the_command_menu(tmp_path):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    runtime = build_runtime(ctx, client=fake.client, offer_every_s=0.05)
    task = asyncio.create_task(runtime.run())
    try:
        fake.say(OWNER, "how are you")
        assert await until(lambda: fake.texts(OWNER))
        assert fake.texts(OWNER)[0] == "[laptop] Fine, thanks."
        assert "note" in [c["command"] for c in fake.commands]
    finally:
        runtime.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_lockdown_closes_the_chat_and_lifting_it_reopens(tmp_path):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    runtime = build_runtime(ctx, client=fake.client, offer_every_s=0.05)
    task = asyncio.create_task(runtime.run())
    try:
        ctx.services["lockdown"].engage("test")
        assert runtime.gateway.suspended is True
        fake.say(OWNER, "are you there")
        await asyncio.sleep(0.2)
        assert fake.texts(OWNER) == []  # dropped, no reply
        ctx.services["lockdown"].release("test")
        assert runtime.gateway.suspended is False
        fake.say(OWNER, "now?")
        assert await until(lambda: fake.texts(OWNER))
    finally:
        runtime.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_starting_inside_a_lockdown_starts_closed(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.services["lockdown"].engage("left on")
    runtime = build_runtime(ctx, client=FakeTelegram().client)
    assert runtime.gateway.suspended is True


async def test_a_job_waiting_for_a_yes_is_offered_without_being_asked_for(tmp_path):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    runtime = build_runtime(ctx, client=fake.client, offer_every_s=0.05)
    task = asyncio.create_task(runtime.run())
    try:
        ctx.services["approvals"].request("files_delete", {"path": "old"}, "Delete the old notes?", risk=2, job_id=3)
        assert await until(lambda: any("Approval needed" in t for t in fake.texts(OWNER)))
    finally:
        runtime.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_private_mode_means_nothing_is_sent(tmp_path):
    ctx = make_ctx(tmp_path, privacy=True)
    runtime = build_runtime(ctx, client=FakeTelegram().client)
    assert runtime.gateway._private_now() is True
    ctx2 = make_ctx(tmp_path / "x")
    ctx2.services["is_private"] = lambda: True
    assert build_runtime(ctx2, client=FakeTelegram().client).gateway._private_now() is True


def test_quiet_hours_follow_the_night_setting(tmp_path):
    ctx = make_ctx(tmp_path)
    runtime = build_runtime(ctx, client=FakeTelegram().client)
    quiet = runtime.push._quiet
    assert quiet(datetime(2026, 10, 15, 23, 30)) is True and quiet(datetime(2026, 10, 15, 12, 0)) is False


async def test_pairing_prints_a_code_and_the_bot_name_and_the_chat_pairs(tmp_path):
    ctx, fake = make_ctx(tmp_path), FakeTelegram()
    data = ctx.store.config.model_dump(mode="json")
    data["telegram"]["allowed_ids"] = []
    ctx.store.update(data)
    text = await pairing_instructions(tmp_path, ctx.secrets, client=fake.client)
    code = text.split("/pair ")[1].split()[0]
    assert "@minty_test_bot" in text and len(code) == 6 and code.isdigit() and TOKEN not in text
    runtime = build_runtime(ctx, client=fake.client)
    await runtime.gateway.handle_update(fake.say(4444, f"/pair {code}"))
    assert 4444 in runtime.gateway.allowed


async def test_pairing_without_a_token_says_how_to_add_one(tmp_path):
    with pytest.raises(SystemExit) as why:
        await pairing_instructions(tmp_path, SecretsStore(tmp_path), client=FakeTelegram().client)
    assert "set-key telegram" in str(why.value)


def test_the_terminal_commands_exist():
    from minty_core.app import parse_args

    assert parse_args(["telegram-pair"]).command == "telegram-pair"
    assert parse_args(["telegram-passphrase"]).command == "telegram-passphrase"
