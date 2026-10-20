from typing import AsyncIterator

import httpx

from minty_core.config import ProviderConfig
from minty_core.httpclient import error_from_response, get_client
from minty_core.llm.types import Event, LLMError, Message, TextDelta, ToolCall, ToolCallEvent, ToolSpec, Usage


def _convert(messages: list[Message]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "user":
            block = {"role": "user", "content": [{"type": "text", "text": m.content}]}
        elif m.role == "assistant":
            content = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for c in m.tool_calls:
                content.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
            if not content:
                continue
            block = {"role": "assistant", "content": content}
        else:
            block = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}]}
        if out and out[-1]["role"] == "user" and block["role"] == "user":
            out[-1]["content"].extend(block["content"])
        else:
            out.append(block)
    return out


class AnthropicProvider:
    def __init__(self, cfg: ProviderConfig, api_key: str, client: httpx.AsyncClient | None = None):
        self.name = cfg.name
        self._model = cfg.model
        self._key = api_key
        self._timeout = httpx.Timeout(cfg.timeout_s, connect=10.0)
        self._url = (cfg.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        self._client = client or get_client()
        self.last_usage: Usage | None = None

    async def stream(self, messages: list[Message], tools: list[ToolSpec]) -> AsyncIterator[Event]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        payload: dict = {"model": self._model, "max_tokens": 1024, "messages": _convert(messages)}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
        try:
            resp = await self._client.post(self._url, json=payload, headers=headers, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise LLMError(f"{self.name}: timed out ({type(e).__name__})", kind="timeout") from e
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name}: {e}", kind="server") from e
        if resp.status_code != 200:
            raise error_from_response(self.name, resp.status_code, resp.headers, resp.text)

        # Build the full event list first before yielding anything
        events: list[Event] = []
        try:
            data = resp.json()
        except (ValueError, AttributeError, TypeError, KeyError) as e:
            raise LLMError(f"{self.name}: malformed response") from e

        u = data.get("usage") if isinstance(data, dict) else None
        if isinstance(u, dict):
            cached = int(u.get("cache_read_input_tokens") or 0)
            self.last_usage = Usage(tokens_in=int(u.get("input_tokens") or 0) + cached
                                    + int(u.get("cache_creation_input_tokens") or 0),
                                    tokens_out=int(u.get("output_tokens") or 0), cached=cached)

        # Check for in-body errors
        if data.get("type") == "error" or "error" in data:
            error_info = data.get("error", {})
            message = error_info.get("message", "unknown error") if isinstance(error_info, dict) else "unknown error"
            raise LLMError(f"{self.name}: {message}")

        # Validate and process content
        try:
            content = data.get("content")
            if content is None:
                raise LLMError(f"{self.name}: malformed response")
            if not isinstance(content, list):
                raise LLMError(f"{self.name}: malformed response")

            for block in content:
                if not isinstance(block, dict):
                    raise LLMError(f"{self.name}: malformed response")
                if block.get("type") == "text" and block.get("text"):
                    events.append(TextDelta(block["text"]))
                elif block.get("type") == "tool_use":
                    block_id = block.get("id")
                    block_name = block.get("name")
                    if block_id is None or block_name is None:
                        raise LLMError(f"{self.name}: malformed response")
                    block_input = block.get("input")
                    if block_input is None:
                        block_input = {}
                    if not isinstance(block_input, dict):
                        raise LLMError(f"{self.name}: malformed response")
                    events.append(ToolCallEvent(ToolCall(block_id, block_name, block_input)))
        except (ValueError, AttributeError, TypeError, KeyError) as e:
            raise LLMError(f"{self.name}: malformed response") from e

        # Yield all events only after successful parsing
        for event in events:
            yield event
