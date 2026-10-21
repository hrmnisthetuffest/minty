"""Telegram, wired into the running app: built from the settings and the key store, closed by lockdown, paired from
the terminal. Nothing here is on by default: telegram.enabled and a token (`minty set-key telegram`) are both needed.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import httpx

from minty_core.brownout import capacity_reply
from minty_core.nightmode import in_quiet_hours
from minty_core.telegram import commands as tg_commands
from minty_core.telegram.api import BotAPI, TelegramError
from minty_core.telegram.approvals import ApprovalBridge, Passphrase
from minty_core.telegram.gateway import PairingStore, TelegramGateway
from minty_core.telegram.push import PushRouter

log = logging.getLogger(__name__)

PAIRING_FILE = "telegram-pairing.json"
PASSPHRASE_FILE = "telegram-passphrase.json"
HELD_FILE = "telegram-held.json"


class TelegramRuntime:
    def __init__(self, gateway: TelegramGateway, bridge: ApprovalBridge, push: PushRouter,
                 menu: list[tuple[str, str]], offer_every_s: float = 5.0, background: list | None = None):
        self.gateway, self.bridge, self.push, self.menu = gateway, bridge, push, menu
        self._offer_every_s = offer_every_s
        self._background = background or []  # coroutine factories run beside the gateway

    async def _offer_loop(self) -> None:
        while True:
            try:
                if not self.gateway.suspended:
                    await self.bridge.offer_new()
            except Exception:  # noqa: BLE001 — a failed offer is retried on the next round
                log.warning("could not offer waiting approvals to Telegram", exc_info=True)
            await asyncio.sleep(self._offer_every_s)

    async def run(self) -> None:
        try:
            await self.gateway.api.set_commands(self.menu)
        except TelegramError as e:
            log.warning("could not publish the Telegram command menu: %s", e)
        tasks = [asyncio.create_task(c) for c in (self.gateway.run(), self.push.run(), self._offer_loop(),
                                                  *(make() for make in self._background))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self) -> None:
        self.gateway.stop()


def build_runtime(ctx, *, client: httpx.AsyncClient | None = None, offer_every_s: float = 5.0) -> TelegramRuntime | None:
    """None when Telegram is off or there is no bot token; the reason is logged, never the token."""
    cfg = ctx.store.config.telegram
    if not cfg.enabled:
        return None
    token = ctx.secrets.get("telegram")
    if not token:
        log.warning("telegram.enabled is on but there is no bot token; run: minty set-key telegram")
        return None
    if client is None:
        from minty_core.httpclient import get_client

        client = get_client()
    directory, services = Path(ctx.config_dir), ctx.services
    store, ops = ctx.store, ctx.store.config.ops

    def private_now() -> bool:
        return bool(store.config.safety.privacy_mode or services.get("is_private", lambda: False)())

    api = BotAPI(token, client)
    gateway = TelegramGateway(api, store, ctx.agent, machine=cfg.machine_name,
                              pairing=PairingStore(directory / PAIRING_FILE), private_now=private_now)
    bridge = ApprovalBridge(gateway, services["approvals"], Passphrase(directory / PASSPHRASE_FILE))
    push = PushRouter(gateway, ctx.bus, state_path=directory / HELD_FILE,
                      quiet=lambda now: in_quiet_hours(now, store.config.ops.quiet_voice_from,
                                                       store.config.ops.quiet_voice_to))
    from minty_core.audio.stt import DynamicSTT
    from minty_core.telegram.voice import ChatPrefs, VoiceNotes

    async def save_memo(text: str) -> str:
        from minty_core.vault.kit import memo_to_inbox

        saved = memo_to_inbox(services.get("vault"), text)  # your own voice note: a note in your inbox
        if saved is not None:
            return saved
        out = await ctx.host.call("journal_add_entry", {"text": f"Memo: {text}"})
        return "Saved to your notes." if not str(out).startswith("Error") else "I couldn't save that just now."

    VoiceNotes(gateway, getattr(getattr(ctx, "pipeline", None), "stt", None) or DynamicSTT(store, ctx.secrets, client),
               lambda: getattr(getattr(ctx, "pipeline", None), "tts", None),
               ChatPrefs(directory / "telegram-chats.json"), memo=save_memo).install()
    from minty_core.telegram.inline import InlineSearch

    InlineSearch(gateway, ctx.host.call, services.get("gists")).install()
    from minty_core.secretscan import SecretScanner
    from minty_core.telegram.intake import Intake, TesseractOCR
    from minty_core.telegram.screenshots import ScreenshotRelay

    from minty_core.telegram.relay import Relay

    from minty_core.telegram.health import HealthImport

    from minty_core.telegram.finance import FinanceTelegram

    scanner, ocr = services.get("scanner") or SecretScanner(), TesseractOCR()
    HealthImport(gateway, lambda: services.get("health") if store.config.health.hc_bridge else None).install()
    FinanceTelegram(gateway, lambda: services.get("finance")).install()
    intake = Intake(gateway, directory / "telegram-inbox", scanner, ocr=ocr)
    from minty_core.butler.filing import file_photo

    from minty_core.study.kit import summarise_upload

    async def file_hook(kind, name, text, caption, forwarded):
        summary = summarise_upload(services.get("study"), kind, text, caption)
        if summary is not None:
            return summary
        provider = services.get("vault")
        return await file_photo(provider, store.config, kind, name, text, caption, forwarded) \
            if provider is not None else None

    intake.file_hook = file_hook
    intake.install()
    background_extra: list = []
    shots = ScreenshotRelay(
        gateway, scanner, ocr=ocr, push=push,
        root=lambda: Path(str((store.config.plugins.get("browser") or {}).get("folder") or "~/Minty-Workspace")))
    from minty_core.telegram.cards import CardRelay

    cards = CardRelay(gateway, root=lambda: directory / "cards")
    workspace = lambda: Path(str((store.config.plugins.get("browser") or {}).get("folder") or "~/Minty-Workspace"))
    Relay(gateway, directory / "telegram-relay", scanner, send_roots=lambda: [workspace()]).install()
    health_hub = services.get("health")
    if health_hub is not None:
        from minty_core.telegram.coach import CoachDelivery

        delivery = CoachDelivery(gateway, push, lambda: services.get("health"))
        delivery.install()
        background_extra.append(lambda: delivery.run(ctx.bus))
        from minty_core.telegram.life import LifeDelivery

        life_delivery = LifeDelivery(gateway, push, lambda: services.get("health"))
        life_delivery.install()
        background_extra.append(lambda: life_delivery.run(ctx.bus))
        from minty_core.health import HealthCommands

        stop = HealthCommands(health_hub).stop_phrase

        async def health_stop(chat_id: int, text: str) -> bool:
            reply = stop(text)
            if reply is None:
                return False
            await gateway.reply(chat_id, reply)
            return True

        gateway.text_hooks.append(health_stop)
    lockdown = services.get("lockdown")
    brownout = services.get("brownout")
    offline = services.get("offline")

    async def brief() -> str:
        compose = services.get("brief_compose")
        text = await compose() if compose is not None else ""
        return text or "I have no brief to give right now."

    def state() -> str:
        if lockdown is not None and lockdown.active:
            return "in lockdown"
        return "in safe mode" if services.get("safe_mode") else "running"

    from minty_core.vault.kit import capture_note, find_text

    async def send_contact(chat_id: int, text: str) -> bool:
        """The check-in message to a contact: fixed text, to one chat id, nothing else."""
        try:
            await gateway.api.send_message(chat_id, text)
            return True
        except TelegramError:
            return False

    services["checkin_send"] = send_contact

    async def vault_capture(kind: str, text: str):
        return capture_note(services.get("vault"), kind, text)

    menu = tg_commands.install(
        gateway, host_call=ctx.host.call, jobs=services.get("jobs"), lockdown=lockdown, brief=brief,
        capacity=(lambda: capacity_reply(brownout.capacity())) if brownout is not None else None,
        skills=getattr(ctx.agent, "skills", None),
        remind=offline.handle if offline is not None else None, state=state, vault_capture=vault_capture,
        vault_find=lambda query: find_text(services.get("vault"), query),
        standing=services.get("standing"),
        safe=(lambda: services["butler"].capture("I'm fine")) if services.get("butler") is not None else None)
    if lockdown is not None:
        gateway.suspended = bool(lockdown.active)
        lockdown.on_engage.append(lambda: setattr(gateway, "suspended", True))
        lockdown.on_release.append(lambda: setattr(gateway, "suspended", False))
    return TelegramRuntime(gateway, bridge, push, menu, offer_every_s, [lambda: shots.run(ctx.bus), lambda: cards.run(ctx.bus), *background_extra])


async def pairing_instructions(directory: Path, secrets, *, client: httpx.AsyncClient | None = None) -> str:
    """A fresh one-time code and what to do with it. Exits with a plain sentence when there is no token."""
    token = secrets.get("telegram")
    if not token:
        raise SystemExit("minty-core: no Telegram bot token yet. Make a bot with @BotFather, then run: "
                         "minty set-key telegram")
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    try:
        try:
            username = (await BotAPI(token, client).get_me()).get("username") or ""
        except TelegramError as e:
            raise SystemExit(f"minty-core: Telegram did not accept the bot token ({e.description or e.code}). "
                             "Check it with @BotFather and run set-key telegram again.") from None
    finally:
        if own:
            await client.aclose()
    code = PairingStore(Path(directory) / PAIRING_FILE).new_code()
    bot = f"@{username}" if username else "your bot"
    return (f"Open {bot} in Telegram and send it:\n  /pair {code}\n"
            "The code works once, for 10 minutes. Only chats paired this way are ever answered.\n"
            "Minty must be running with telegram.enabled on (the Switches page in the panel).")
