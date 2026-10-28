from pathlib import Path

import httpx


class CoreUnavailable(Exception):
    pass


class CoreClient:
    def __init__(self, base_url: str, token_path: Path, client: httpx.AsyncClient | None = None):
        self._base = base_url.rstrip("/")
        self._token_path = Path(token_path)
        self._client = client or httpx.AsyncClient(timeout=15)

    def _token(self) -> str:
        try:
            return self._token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    async def request(self, method: str, path: str, *, params=None, content: bytes | None = None,
                      content_type: str | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token()}"}
        if content_type:
            headers["content-type"] = content_type
        try:
            return await self._client.request(method, self._base + path, params=params, content=content, headers=headers)
        except httpx.HTTPError as e:
            raise CoreUnavailable(str(e)) from e

    async def open(self, path: str, *, headers: dict | None = None) -> httpx.Response:
        """A streamed GET (the caller closes it): for clips, which are passed on as they arrive and with their byte ranges."""
        request = self._client.build_request("GET", self._base + path, headers={**(headers or {}), "Authorization": f"Bearer {self._token()}"})
        try:
            return await self._client.send(request, stream=True)
        except httpx.HTTPError as e:
            raise CoreUnavailable(str(e)) from e

    def ws_url(self) -> str:
        return self._base.replace("http", "ws", 1) + "/events"  # no secret in an address: addresses get logged

    def ws_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}
