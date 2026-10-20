"""Free image generation (663) through Cloudflare Workers AI's daily free allowance. Off until `create.images` is on and you
have set your own free Cloudflare token and account id. The prompt is the only thing sent; one containing a key, a password or
a personal id is refused unsent. A local daily cap keeps you inside the free allowance, and a picture is saved as a NEW file."""
from __future__ import annotations

import base64
import binascii
import re
import time
from pathlib import Path
from typing import Awaitable, Callable

from minty_core.create.db import CreateDB
from minty_core.create.documents import save
from minty_core.secretscan import SecretScanner, pii_findings

MODEL = "@cf/black-forest-labs/flux-1-schnell"
URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/" + MODEL
MAX_PROMPT = 400
_ACCOUNT = re.compile(r"[0-9a-f]{32}")


def _kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class ImageGen:
    def __init__(self, post: Callable[[str, dict, dict], Awaitable[tuple[int, object]]],
                 secret: Callable[[str], str | None], db: CreateDB, folder: Path, clock: Callable[[], float] = time.time,
                 daily_cap: int = 8):
        self._post, self._secret, self.db, self.folder, self._clock, self.cap = post, secret, db, Path(folder), clock, daily_cap

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._clock()))

    def used(self) -> int:
        day, _, n = self.db.meta("img_day").partition(":")
        return int(n or 0) if day == self._today() else 0

    def left(self) -> int:
        return max(0, self.cap - self.used())

    async def generate(self, prompt: str) -> Path:
        prompt = " ".join(str(prompt).split())
        if not prompt or len(prompt) > MAX_PROMPT:
            raise ValueError(f"Describe the picture in 1 to {MAX_PROMPT} characters.")
        if SecretScanner().scan(prompt) or pii_findings(prompt):
            raise ValueError("That prompt looks like it holds a key, a password or a personal number, so I did not send it.")
        token, account = self._secret("cf_token"), self._secret("cf_account")
        if not token or not account:
            raise ValueError("Image making needs your own free Cloudflare account (Workers AI). Make a token, then run: "
                             "minty-core set-key plugin.create.cf_token and minty-core set-key plugin.create.cf_account.")
        if not _ACCOUNT.fullmatch(account):
            raise ValueError("The Cloudflare account id looks wrong (it is 32 letters and digits).")
        if self.left() <= 0:
            raise ValueError(f"The image allowance is used today ({self.cap} of {self.cap}). Try tomorrow.")
        status, payload = await self._post(URL.format(account=account), {"Authorization": f"Bearer {token}"},
                                           {"prompt": prompt, "steps": 4})
        if status == 429:
            raise ValueError("Cloudflare says the free image allowance is used up for now.")
        if status in (401, 403):
            raise ValueError("Cloudflare rejected the token.")
        data = None
        if status == 200:
            try:
                if isinstance(payload, (bytes, bytearray)):
                    data = bytes(payload)
                else:
                    data = base64.b64decode(str(((payload or {}).get("result") or {}).get("image") or ""), validate=True)
            except (binascii.Error, ValueError, AttributeError):
                data = None
        ext = _kind(data) if data else None
        if ext is None:
            raise ValueError("Cloudflare could not make the picture just now.")
        path = save(self.folder, prompt[:40], data, ext)
        self.db.set_meta("img_day", f"{self._today()}:{self.used() + 1}")
        return path
