from __future__ import annotations

import re
import time

from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol, Union


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    extra: dict = field(default_factory=dict)  # provider data to send back with the call (Gemini's thought signature)


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    private: bool = False
    ts: float = field(default_factory=time.time)
    chat: bool = False  # part of a Telegram chat: kept longer than the voice window, in tiers (agent.tier_history)


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallEvent:
    call: ToolCall


Event = Union[TextDelta, ToolCallEvent]


@dataclass
class Usage:
    """Tokens a provider reported for one call (completion tokens include reasoning tokens)."""
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning: int = 0
    cached: int = 0  # input tokens served from the provider's prompt cache


ErrorKind = Literal["rate_limit", "too_large", "tool_not_offered", "server", "timeout", "auth", "other"]
REQUEST_SHAPED: frozenset[str] = frozenset({"too_large", "tool_not_offered"})  # fix the request, not the provider

_TOOL_NOT_OFFERED = re.compile(r"(?:tool call validation failed|not in request\.tools)", re.IGNORECASE)
_TOOL_NAME = re.compile(r"call tool ['\"`]?([\w.-]+)['\"`]?", re.IGNORECASE)
_TOO_LARGE = re.compile(r"request too large|context.length|maximum context|too many tokens|reduce the length|"
                        r"reduce your message size|prompt is too long", re.IGNORECASE)


def classify(status: int | None, message: str) -> ErrorKind:
    """What kind of failure an error is, from its HTTP status and text."""
    if _TOOL_NOT_OFFERED.search(message):
        return "tool_not_offered"
    if status == 413 or (status in (None, 400) and _TOO_LARGE.search(message)):
        return "too_large"
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "auth"
    if status in (408, 504):
        return "timeout"
    if status is not None and 500 <= status < 600:
        return "server"
    return "other"


class LLMError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None,
                 kind: ErrorKind | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.kind: ErrorKind = kind or classify(status, message)
        self.provider = ""  # set by the router: which provider raised it
        self.rate = None  # ratelimit.RateInfo from the response headers, when the provider sent any


class RequestTooLarge(LLMError):
    """The request is over the provider's size/token-per-minute limit: trim and retry, do not cool the provider."""


class ToolNotOffered(LLMError):
    """The model called a tool the request did not offer: load its toolkit and retry the same provider."""

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None,
                 kind: ErrorKind | None = None):
        super().__init__(message, status=status, retry_after=retry_after, kind="tool_not_offered")
        match = _TOOL_NAME.search(message)
        self.tool = match.group(1) if match else ""


class DeadlineExceeded(LLMError):
    """No provider produced a first token within the turn's deadline."""

    def __init__(self, message: str, **_):
        super().__init__(message, kind="timeout")


def llm_error(message: str, *, status: int | None = None, retry_after: float | None = None) -> LLMError:
    """An LLMError of the right type for its cause."""
    kind = classify(status, message)
    cls = {"too_large": RequestTooLarge, "tool_not_offered": ToolNotOffered}.get(kind, LLMError)
    return cls(message, status=status, retry_after=retry_after, kind=kind)


class Provider(Protocol):
    name: str

    def stream(self, messages: list[Message], tools: list[ToolSpec]) -> AsyncIterator[Event]: ...
