"""File relay between this machine's bot and another machine's bot (556). Off until switched on at both ends.

Telegram only: the peer is another Minty bot whose numeric id you listed in telegram.relay_peers. Every relay
message starts with an envelope line (`MINTY-RELAY/1 {from, to, id, hop, intent}`). A message is accepted only from a
listed peer, addressed to this machine, with hop < 2, an id not seen in 10 minutes, and no more than 5 a minute per
peer. An acknowledgement is never answered, so two bots cannot loop. A received file is only put in a quarantine
folder and announced; nothing in it or in the message is read as an instruction.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from minty_core.telegram.api import MAX_DOWNLOAD, TelegramError
from minty_core.telegram.intake import check_text, safe_name

log = logging.getLogger(__name__)

PREFIX = "MINTY-RELAY/1 "
MAX_HOP = 2
DEDUPE_S = 600.0
PER_MINUTE = 5
MAX_UPLOAD = 50 * 1024 * 1024
KEEP = 100


def parse(text: str) -> dict | None:
    if not text.startswith(PREFIX):
        return None
    try:
        env = json.loads(text[len(PREFIX):])
        return {"from": str(env["from"]), "to": str(env["to"]), "id": str(env["id"])[:64],
                "hop": int(env["hop"]), "intent": str(env["intent"])}
    except (ValueError, KeyError, TypeError):
        return None


def envelope(frm: str, to: str, id_: str, hop: int, intent: str) -> str:
    return PREFIX + json.dumps({"from": frm, "to": to, "id": id_, "hop": hop, "intent": intent})


class Relay:
    def __init__(self, gateway, folder: Path, scanner, *, send_roots: Callable[[], list[Path]],
                 clock: Callable[[], float] = time.time):
        self.gw, self.folder, self.scanner, self._roots, self._clock = gateway, Path(folder), scanner, send_roots, clock
        self._seen: dict[str, float] = {}
        self._arrivals: dict[int, list[float]] = {}

    def install(self) -> None:
        self.gw.bot_hooks.append(self.on_bot_message)
        self.gw.handlers["/relay"] = self.command

    # ---- config -----------------------------------------------------------------------------------
    @property
    def _cfg(self):
        return self.gw.store.config.telegram

    def _peer_id(self, name: str) -> int | None:
        peers = {k.lower(): int(v) for k, v in self._cfg.relay_peers.items()}
        return peers.get(name.strip().lower())

    # ---- receiving --------------------------------------------------------------------------------
    async def on_bot_message(self, peer_id: int, message: dict) -> bool:
        """Always True: a message from a bot is never handled as a chat."""
        if not self._cfg.relay or peer_id not in {int(v) for v in self._cfg.relay_peers.values()}:
            return True
        env = parse(str(message.get("caption") or message.get("text") or ""))
        now = self._clock()
        if env is None or env["to"].lower() != self.gw.machine.lower() or env["hop"] >= MAX_HOP:
            return True
        self._seen = {k: t for k, t in self._seen.items() if now - t < DEDUPE_S}
        recent = [t for t in self._arrivals.get(peer_id, []) if now - t < 60.0]
        if env["id"] in self._seen or len(recent) >= PER_MINUTE:
            return True
        self._seen[env["id"]] = now
        self._arrivals[peer_id] = recent + [now]
        if env["intent"] == "ack":
            return True  # an acknowledgement ends the exchange
        document = message.get("document")
        if env["intent"] != "file" or not document:
            return True
        name = safe_name(str(document.get("file_name") or "file"))
        if int(document.get("file_size") or 0) > MAX_DOWNLOAD:
            await self._tell(f"{env['from']} tried to send {name}, but it is too big for me (20 MB at most).")
            return True
        try:
            info = await self.gw.api.get_file(str(document.get("file_id")))
            data = await self.gw.api.download(str(info.get("file_path")), MAX_DOWNLOAD)
        except TelegramError as e:
            log.warning("relay download failed: %s", type(e).__name__)
            return True
        path = self._save(name, data)
        await self._tell(f"{env['from']} sent {name} ({len(data) // 1024 + 1} KB). It is saved in {path.parent.name}/ "
                         "and I didn't open it.")
        try:
            await self.gw.sender.send(peer_id, envelope(self.gw.machine, env["from"], env["id"], env["hop"] + 1,
                                                        "ack"))
        except TelegramError as e:
            log.warning("relay ack failed: %s", e)
        return True

    def _save(self, name: str, data: bytes) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        path = self.folder / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(self._clock()))}-{os.urandom(3).hex()}-{name}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        files = sorted((p for p in self.folder.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime_ns)
        for old in files[:max(0, len(files) - KEEP)]:
            try:
                old.unlink()
            except OSError:
                pass
        return path

    async def _tell(self, text: str) -> None:
        for chat in sorted(self.gw.allowed):
            try:
                await self.gw.reply(chat, text)
            except TelegramError as e:
                log.warning("could not tell you about a relay message: %s", e)

    # ---- sending ----------------------------------------------------------------------------------
    def _inside_roots(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            return not path.is_symlink() and any(resolved.parent == Path(r).expanduser().resolve() or
                                                  Path(r).expanduser().resolve() in resolved.parents
                                                  for r in self._roots())
        except OSError:
            return False

    async def send_file(self, peer: str, path: Path) -> str:
        if not self._cfg.relay:
            return "File relay is off. Turn on telegram.relay on both machines first."
        peer_id = self._peer_id(peer)
        if peer_id is None:
            known = ", ".join(sorted(self._cfg.relay_peers)) or "none set"
            return f"I don't know a machine called {peer}. Known: {known}."
        path = Path(path).expanduser()
        if self.gw._private_now():
            return "I'm in private mode, so nothing leaves the laptop."
        if not path.is_file() or not self._inside_roots(path):
            return "I only send files that are in my workspace folder."
        if path.stat().st_size > MAX_UPLOAD:
            return "That file is too big (50 MB at most)."
        data = path.read_bytes()
        if path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".log", ".env", ".yaml", ".yml", ".toml", ".ini"}:
            why, _ = check_text(self.scanner, data[:2_000_000].decode("utf-8", errors="replace"))
            if why:
                return f"I won't send {path.name}: it contains {', '.join(why)}. Take the key out first."
        caption = envelope(self.gw.machine, peer.lower(), os.urandom(6).hex(), 0, "file")
        try:
            await self.gw.sender.send_media(peer_id, "document", data, safe_name(path.name), caption)
        except TelegramError as e:
            return f"Telegram would not take it ({e.description})."
        return f"Sent {path.name} to {peer}."

    async def command(self, chat_id: int, args: str) -> str:
        parts = args.split(None, 1)
        if len(parts) != 2:
            return "Say which machine and which file, for example: /relay pc notes.md"
        name = parts[1].strip()
        for root in self._roots():
            candidate = Path(root).expanduser() / Path(name).name
            if candidate.is_file():
                return await self.send_file(parts[0], candidate)
        return f"I can't find {name} in my workspace folder."
