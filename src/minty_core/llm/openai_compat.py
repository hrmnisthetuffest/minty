import json
import time
from typing import AsyncIterator

import httpx

from minty_core.config import ProviderConfig
from minty_core.httpclient import error_from_response, get_client
from minty_core.llm.ratelimit import parse_rate_headers
from minty_core.llm.types import (Event, LLMError, Message, TextDelta, ToolCall, ToolCallEvent, ToolSpec, Usage,
                                   llm_error)


# Request fields the provider owns; ProviderConfig.extra can never replace them.
_RESERVED = frozenset({
    "model", "messages", "stream", "tools", "tool_choice", "functions", "function_call", "stream_options",
})


# Gemini rejects replayed tool calls without the thought signature it issued. Calls another provider made have
# none, so they carry Google's documented placeholder that skips the check.
_GEMINI_SKIP = {"google": {"thought_signature": "skip_thought_signature_validator"}}


def _serialise(m: Message, gemini: bool = False) -> dict:
    if m.role == "assistant" and m.tool_calls:
        calls = []
        for c in m.tool_calls:
            call = {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            extra = c.extra.get("extra_content") or (_GEMINI_SKIP if gemini else None)
            if extra:
                call["extra_content"] = extra
            calls.append(call)
        return {"role": "assistant", "content": m.content or None, "tool_calls": calls}
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    return {"role": m.role, "content": m.content}


def _usage(u: dict) -> Usage:
    def n(value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
    return Usage(tokens_in=n(u.get("prompt_tokens")), tokens_out=n(u.get("completion_tokens")),
                 reasoning=n((u.get("completion_tokens_details") or {}).get("reasoning_tokens")),
                 cached=n((u.get("prompt_tokens_details") or {}).get("cached_tokens")))


class OpenAICompatProvider:
    def __init__(self, cfg: ProviderConfig, api_key: str, client: httpx.AsyncClient | None = None):
        self.name = cfg.name
        self._model = cfg.model
        self._extra = {k: v for k, v in cfg.extra.items() if k not in _RESERVED}
        self._key = api_key
        self._timeout = httpx.Timeout(cfg.timeout_s, connect=10.0)
        self._url = cfg.base_url.rstrip("/") + "/chat/completions"
        self._client = client or get_client()
        self.last_rate = None  # what the latest response's rate-limit headers said (ratelimit.RateInfo)
        self.last_usage: Usage | None = None  # tokens the provider reported for the latest call
        self._ask_usage = cfg.effective_stream_usage()

    async def stream(self, messages: list[Message], tools: list[ToolSpec]) -> AsyncIterator[Event]:
        gemini = "generativelanguage.googleapis.com" in self._url
        payload: dict = {"model": self._model, "stream": True, "messages": [_serialise(m, gemini) for m in messages],
                         "temperature": 0.6}  # provider defaults (often 1.0) can ramble into gibberish
        payload.update(self._extra)
        if self._ask_usage:
            payload["stream_options"] = {"include_usage": True}
        self.last_usage = None
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        pending: dict[int, dict] = {}
        try:
            async with self._client.stream("POST", self._url, json=payload, headers=headers, timeout=self._timeout) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    err = error_from_response(self.name, resp.status_code, resp.headers, body)
                    self.last_rate = err.rate
                    raise err
                self.last_rate = parse_rate_headers(resp.headers, now=time.time())
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as e:
                        raise LLMError(f"{self.name}: malformed stream chunk") from e
                    if "error" in chunk:
                        error_info = chunk["error"]
                        message = error_info.get("message", "unknown error")
                        code = error_info.get("code")
                        status = code if isinstance(code, int) else None
                        raise llm_error(f"{self.name}: {message}", status=status)
                    reported = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
                    if isinstance(reported, dict):
                        self.last_usage = _usage(reported)
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield TextDelta(delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            slot = pending.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": "", "extra": {}})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            if tc.get("extra_content"):
                                slot["extra"] = {"extra_content": tc["extra_content"]}
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
        except httpx.TimeoutException as e:
            raise LLMError(f"{self.name}: timed out ({type(e).__name__})", kind="timeout") from e
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name}: {e}", kind="server") from e
        for index in sorted(pending):
            slot = pending[index]
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError as e:
                raise LLMError(f"{self.name}: malformed tool arguments for {slot['name']}") from e
            yield ToolCallEvent(ToolCall(slot["id"] or f"call_{index}", slot["name"], args, slot["extra"]))
