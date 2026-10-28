"""Publishing to Instagram through Meta's official Graph API (a professional account, your own token). No scraping and no logging in as
you: only the documented content-publishing calls.

Reel: create a resumable container, upload the file to rupload.facebook.com, wait until the container is FINISHED, publish it, read
back its permalink. Carousel: one container per picture (each needs a public https address), then a carousel container, then publish.

Written from Meta's published documentation and tested only against a pretend server: it has not run against a real account. The
token is sent only to Meta's hosts. An error after the publish request was made is "unknown" (it may have gone out), never "failed".
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

HOSTS = ("graph.instagram.com", "graph.facebook.com")
UPLOAD_HOST = "rupload.facebook.com"
DONE, BAD = "FINISHED", ("ERROR", "EXPIRED")


class InstagramError(Exception):
    """published: False (nothing went out), None (it may have: the publish request was made) ."""

    def __init__(self, message: str, published: bool | None = False):
        super().__init__(message)
        self.published = published


@dataclass(frozen=True)
class Published:
    media_id: str
    permalink: str


class Instagram:
    def __init__(self, user_id: str, token: str, *, host: str = "graph.instagram.com", version: str = "v21.0",
                 client: httpx.AsyncClient | None = None, poll_s: float = 3.0, timeout_s: float = 300.0):
        if host not in HOSTS:
            raise ValueError(f"host must be one of {', '.join(HOSTS)}: the token is only ever sent to Meta")
        if not re.fullmatch(r"\d{3,30}", str(user_id)):
            raise ValueError("the Instagram account id is a number")
        self._id, self._token, self._host, self._v = str(user_id), token, host, version
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))
        self._poll_s, self._timeout_s = poll_s, timeout_s

    def _clean(self, text: str) -> str:
        return " ".join(str(text).replace(self._token, "***").split())[:300] if self._token else str(text)[:300]

    def _graph(self, path: str) -> str:
        return f"https://{self._host}/{self._v}/{path}"

    async def _json(self, method: str, url: str, *, published: bool | None = False, **kw) -> dict:
        headers = {"Authorization": f"Bearer {self._token}", **kw.pop("headers", {})}
        try:
            resp = await self._client.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise InstagramError(f"could not reach Instagram ({type(e).__name__})", published) from None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            message = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
            raise InstagramError(f"Instagram refused it ({resp.status_code}): {self._clean(message or 'no reason given')}", published)
        return data if isinstance(data, dict) else {}

    async def _container(self, payload: dict) -> str:
        data = await self._json("POST", self._graph(f"{self._id}/media"), json=payload)
        if not data.get("id"):
            raise InstagramError("Instagram did not give a container id")
        return str(data["id"])

    async def _wait(self, container: str) -> None:
        waited = 0.0
        while True:
            data = await self._json("GET", self._graph(f"{container}") + "?fields=status_code,status")
            status = str(data.get("status_code") or "")
            if status == DONE:
                return
            if status in BAD:
                raise InstagramError(f"Instagram could not process it (status {status}{': ' + self._clean(data['status']) if data.get('status') else ''})")
            if waited >= self._timeout_s:
                raise InstagramError("Instagram did not finish processing it in time")
            await asyncio.sleep(self._poll_s)
            waited += max(self._poll_s, 0.01)

    async def _publish(self, container: str) -> Published:
        # from here on the post may exist even if the answer is an error or never arrives
        data = await self._json("POST", self._graph(f"{self._id}/media_publish"), json={"creation_id": container}, published=None)
        media_id = str(data.get("id") or "")
        if not media_id:
            raise InstagramError("Instagram answered the publish without an id", None)
        link = ""
        try:
            got = await self._json("GET", self._graph(media_id) + "?fields=permalink")
            link = str(got.get("permalink") or "")
        except InstagramError:
            pass  # published, only the link could not be read back
        return Published(media_id, link)

    async def publish_reel(self, video: Path, caption: str, *, ai_generated: bool = True, share_to_feed: bool = True) -> Published:
        video = Path(video)
        data = video.read_bytes()
        payload = {"media_type": "REELS", "upload_type": "resumable", "caption": caption, "share_to_feed": share_to_feed}
        if ai_generated:
            payload["is_ai_generated"] = True
        container = await self._container(payload)
        try:
            resp = await self._client.post(f"https://{UPLOAD_HOST}/ig-api-upload/{self._v}/{container}", content=data,
                                           headers={"Authorization": f"OAuth {self._token}", "offset": "0", "file_size": str(len(data))})
        except httpx.HTTPError as e:
            raise InstagramError(f"the upload did not complete ({type(e).__name__})") from None
        if resp.status_code >= 400:
            raise InstagramError(f"the upload was refused ({resp.status_code})")
        await self._wait(container)
        return await self._publish(container)

    async def publish_carousel(self, image_urls: list[str], caption: str, *, ai_generated: bool = True) -> Published:
        if not 2 <= len(image_urls) <= 20:
            raise ValueError("a carousel has 2 to 20 pictures")
        children = []
        for url in image_urls:
            payload = {"image_url": url, "is_carousel_item": True}
            if ai_generated:
                payload["is_ai_generated"] = True
            children.append(await self._container(payload))
        payload = {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}
        container = await self._container(payload)
        await self._wait(container)
        return await self._publish(container)
