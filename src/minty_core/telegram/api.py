"""A small Telegram Bot API client over httpx (long polling; no inbound port).

The bot token is in every request URL, so it must never reach a log line or an error message: errors are built from
the API's own description and the method name only. httpx logs every request URL at INFO, so a filter on its loggers
rewrites `/bot<token>/` before any handler sees the line, whatever the log level is set to.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

API = "https://api.telegram.org"
MAX_DOWNLOAD = 20 * 1024 * 1024  # what getFile serves to a bot
_TOKEN_IN_URL = re.compile(r"/bot\d+:[A-Za-z0-9_-]+/")


class _HideToken(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 — a malformed record is passed through untouched
            return True
        if "/bot" in text:
            record.msg, record.args = _TOKEN_IN_URL.sub("/bot<token>/", text), None
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_HideToken())


class TelegramError(Exception):
    def __init__(self, method: str, code: int | None, description: str, retry_after: float | None = None):
        super().__init__(f"telegram {method}: {description}" + (f" (HTTP {code})" if code else ""))
        self.method, self.code, self.description, self.retry_after = method, code, description, retry_after


class BotAPI:
    def __init__(self, token: str, client: httpx.AsyncClient, base: str = API, sleep=asyncio.sleep,
                 max_retries: int = 2):
        if not token or ":" not in token:
            raise ValueError("a Telegram bot token looks like 123456:ABC...")
        self._token, self._client, self._base = token, client, base.rstrip("/")
        self._sleep, self._max_retries = sleep, max_retries
        self.me: dict | None = None

    def _url(self, method: str) -> str:
        return f"{self._base}/bot{self._token}/{method}"

    async def call(self, method: str, http_timeout: float = 15.0, _files: dict | None = None, **params: Any) -> Any:
        """The `result` of a Bot API call. A 429 is waited out (up to `max_retries`); anything else raises
        TelegramError. Parameters are sent as JSON (a multipart form when files go up); None values are left out."""
        body = {k: v for k, v in params.items() if v is not None}
        for attempt in range(self._max_retries + 1):
            try:
                if _files:
                    form = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v).lower() if isinstance(v, bool)
                                else str(v)) for k, v in body.items()}
                    resp = await self._client.post(self._url(method), data=form, files=_files, timeout=http_timeout)
                else:
                    resp = await self._client.post(self._url(method), json=body, timeout=http_timeout)
            except httpx.HTTPError as e:  # the exception text may contain the URL (the token): do not pass it on
                raise TelegramError(method, None, f"network error ({type(e).__name__})") from None
            try:
                data = resp.json()
            except ValueError:
                raise TelegramError(method, resp.status_code, "the reply was not JSON") from None
            if data.get("ok"):
                return data.get("result")
            params_ = data.get("parameters") or {}
            retry = params_.get("retry_after")
            if resp.status_code == 429 and retry is not None and attempt < self._max_retries:
                await self._sleep(min(float(retry), 60.0))
                continue
            raise TelegramError(method, data.get("error_code") or resp.status_code,
                                str(data.get("description") or "error")[:200],
                                float(retry) if retry is not None else None)
        raise TelegramError(method, 429, "rate limited")

    # ---- the handful of methods the gateway uses ----------------------------------------------------
    async def get_me(self) -> dict:
        self.me = await self.call("getMe")
        return self.me

    async def get_updates(self, offset: int | None, timeout_s: int = 25) -> list[dict]:
        return await self.call("getUpdates", http_timeout=timeout_s + 10, offset=offset, timeout=timeout_s,
                               allowed_updates=["message", "callback_query", "poll_answer", "inline_query"]) or []

    async def send_message(self, chat_id: int, text: str, *, reply_markup: dict | None = None,
                           silent: bool = False) -> dict:
        return await self.call("sendMessage", chat_id=chat_id, text=text[:4096], reply_markup=reply_markup,
                               disable_notification=True if silent else None)

    async def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict:
        return await self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4096],
                               reply_markup=reply_markup)

    async def typing(self, chat_id: int) -> None:
        await self.call("sendChatAction", chat_id=chat_id, action="typing")

    async def answer_callback(self, callback_id: str, text: str = "", alert: bool = False) -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200] or None,
                        show_alert=True if alert else None)

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        await self.call("setMyCommands", commands=[{"command": c[:32], "description": d[:256]}
                                                    for c, d in commands[:100]])

    # ---- files ------------------------------------------------------------------------------------
    async def get_file(self, file_id: str) -> dict:
        return await self.call("getFile", file_id=file_id)

    async def download(self, file_path: str, max_bytes: int = MAX_DOWNLOAD) -> bytes:
        url = f"{self._base}/file/bot{self._token}/{file_path}"
        try:
            resp = await self._client.get(url, timeout=60.0)
        except httpx.HTTPError as e:  # the exception text may contain the URL (the token)
            raise TelegramError("download", None, f"network error ({type(e).__name__})") from None
        if resp.status_code != 200:
            raise TelegramError("download", resp.status_code, "the file could not be downloaded")
        if len(resp.content) > max_bytes:
            raise TelegramError("download", None, "the file is too large")
        return resp.content

    async def send_voice(self, chat_id: int, ogg: bytes, caption: str = "", silent: bool = False) -> dict:
        return await self.call("sendVoice", 60.0, {"voice": ("voice.ogg", ogg, "audio/ogg")}, chat_id=chat_id,
                               caption=caption[:1024] or None, disable_notification=True if silent else None)

    async def send_photo(self, chat_id: int, data: bytes, filename: str = "photo.png", caption: str = "",
                         silent: bool = False) -> dict:
        return await self.call("sendPhoto", 60.0, {"photo": (filename, data)}, chat_id=chat_id,
                               caption=caption[:1024] or None, disable_notification=True if silent else None)

    async def send_video(self, chat_id: int, data: bytes, filename: str = "video.mp4", caption: str = "",
                         silent: bool = False) -> dict:
        return await self.call("sendVideo", 120.0, {"video": (filename, data)}, chat_id=chat_id, supports_streaming=True,
                               caption=caption[:1024] or None, disable_notification=True if silent else None)

    async def send_document(self, chat_id: int, data: bytes, filename: str, caption: str = "",
                            silent: bool = False) -> dict:
        return await self.call("sendDocument", 60.0, {"document": (filename, data)}, chat_id=chat_id,
                               caption=caption[:1024] or None, disable_notification=True if silent else None)

    async def answer_inline_query(self, inline_query_id: str, results: list[dict], cache_time: int = 0) -> None:
        await self.call("answerInlineQuery", inline_query_id=inline_query_id, results=results[:20],
                        cache_time=cache_time, is_personal=True)

    async def send_poll(self, chat_id: int, question: str, options: list[str]) -> dict:
        return await self.call("sendPoll", chat_id=chat_id, question=question[:300], options=options[:10],
                               is_anonymous=False)
