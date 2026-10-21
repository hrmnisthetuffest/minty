import asyncio
import contextlib
import dataclasses
import json
import logging
import math
import re
import time
from collections import deque
from pathlib import Path
from typing import AsyncIterator, Callable

import httpx

from minty_core.config import ConfigStore, ProviderConfig
from minty_core.fsutil import atomic_write_text
from minty_core.httpclient import get_client
from minty_core.keystore import SecretsStore
from minty_core.llm.anthropic import AnthropicProvider
from minty_core.llm.claude_cli import ClaudeCliProvider, claude_command
from minty_core.llm.openai_compat import OpenAICompatProvider
from minty_core.llm.types import (REQUEST_SHAPED, DeadlineExceeded, Event, LLMError, Message, Provider, TextDelta,
                                   ToolCallEvent, ToolSpec, Usage, llm_error)
from minty_core.complexity import classify_level
from minty_core.failures import FaultInjector, failing_stream
from minty_core.frugal import frugal_params
from minty_core.pseudonym import Pseudonymizer, StreamUnmasker
from minty_core.trace import NullTracer, annotate_turn, turn_fields

log = logging.getLogger(__name__)

# how speech-to-text tends to spell model names
ALIASES = {"kimmy": "kimi", "kimy": "kimi", "jemma": "gemma", "gemeni": "gemini", "nematron": "nemotron",
           "nemetron": "nemotron", "grok": "groq", "lama": "llama", "chatgpt": "gpt", "claud": "claude",
           "clawed": "claude"}
_FILLER = {"the", "model", "models", "ai", "brain", "a", "an", "to", "please", "one", "instead", "now", "it", "this",
           "that", "me", "my", "for", "and", "on", "in", "of", "with"}


def build_provider(cfg: ProviderConfig, key: str) -> Provider:
    if cfg.kind == "claude_cli":
        return ClaudeCliProvider(cfg)
    if cfg.kind == "anthropic":
        return AnthropicProvider(cfg, key)
    return OpenAICompatProvider(cfg, key)


MAX_BACKOFF_S = 900.0  # longest rest after repeated failures (a key error keeps its own hour)
HEALTH_WINDOW = 10  # recent calls per provider that decide its health
HEALTH_MIN_CALLS = 3  # fewer calls than this say nothing about health
HEALTH_TTL_S = 1800.0  # older results are forgotten, so a sunk provider gets tried again
DEMOTE_S = 600.0  # a provider with no success in its recent calls goes to the back this long, then is probed
LEARN_MIN = 5  # calls of one kind before what was learned about a provider counts
LEARN_ALPHA = 0.3  # weight of the newest call in the learned averages
LEARN_SAVE_S = 30.0  # the learned table is written at most this often


class ProviderRouter:
    supports_request_errors = True  # stream(request_errors="raise") hands request-shaped errors to the caller

    def __init__(
        self,
        store: ConfigStore,
        secrets: SecretsStore,
        factory: Callable[[ProviderConfig, str], Provider] = build_provider,
        clock: Callable[[], float] = time.monotonic,
        tracer=None,
        state_path: Path | None = None,
        wall: Callable[[], float] = time.time,
        ledger=None,
    ):
        self._store = store
        self.claude_pick = None  # (model, effort) asked for by name for this request (agent sets and clears it)
        self._armed = None
        self._faults = FaultInjector()
        self._secrets = secrets
        self._factory = factory
        self._clock = clock
        self._cooldown: dict[str, float] = {}
        self._strikes: dict[str, int] = {}  # failures in a row; each doubles the next rest (a rate-limited
        # favourite would otherwise cost a slow failed request every minute)
        self._last_error: dict[str, str] = {}
        self._last_kind: dict[str, str] = {}
        self._health: dict[str, deque] = {}  # name -> recent (at, ok, first_token_ms)
        self._demoted: dict[str, float] = {}  # name -> until
        self._probe: dict[str, str] = {}  # name -> what the start-up key probe found ("ok", "key rejected", ...)
        self._state_path, self._wall = state_path, wall
        self._learned: dict[str, dict[str, list]] = {}  # request kind -> provider -> [calls, success, first-token ms]
        self._learn_saved = 0.0
        self.ledger = ledger  # llm.ledger.CapacityLedger or None: what each free tier has left today
        self.capacity_low: Callable[[], bool] = lambda: False  # set by the app: brownout -> frugal F1 by itself
        self._load_state()
        self.active: tuple[str, str] | None = None  # (provider, model) that produced the latest answer
        self.tracer = tracer or NullTracer()

    def _key(self, cfg: ProviderConfig) -> str | None:
        return self._secrets.get(cfg.key_name or cfg.name)

    def usable(self) -> list[ProviderConfig]:
        """Providers that could answer right now if asked (enabled and keyed), in configured order."""
        return [c for c in self._store.config.providers
                if c.enabled and (self._key(c) or not c.requires_key) and self._present(c)]

    @staticmethod
    def _present(cfg: ProviderConfig) -> bool:
        """A Claude Code provider needs the `claude` program on this machine."""
        return cfg.kind != "claude_cli" or claude_command(cfg) is not None

    def find(self, spoken: str) -> ProviderConfig | None:
        """The usable provider a spoken name refers to: 'gemini', 'kimi', 'gpt oss', 'nvidia deep'..."""
        words = [{w, ALIASES.get(w, w)} for w in re.findall(r"[a-z0-9]+", spoken.lower()) if w not in _FILLER]
        if not words:
            return None
        best, best_score = None, 0
        for cfg in self.usable():
            tokens = set(re.findall(r"[a-z0-9]+", f"{cfg.name} {cfg.model}".lower()))
            score = sum(1 for spellings in words
                        if any(t == w or (len(w) >= 3 and t.startswith(w)) for w in spellings for t in tokens))
            if score == len(words) and score > best_score:
                best, best_score = cfg, score
        return best

    # ---- what survives a restart (wall-clock deadlines; the monotonic clock restarts with the process) -------
    def _persisting(self) -> bool:
        return self._state_path is not None and self._store.config.router.persist_state

    def _load_state(self) -> None:
        if not self._persisting() or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            now_wall, now = self._wall(), self._clock()
            for name, until in data.get("cooldown", {}).items():
                if float(until) > now_wall:
                    self._cooldown[name] = now + float(until) - now_wall
            for name, until in data.get("demoted", {}).items():
                if float(until) > now_wall:
                    self._demoted[name] = now + float(until) - now_wall
            self._strikes.update({str(k): int(v) for k, v in data.get("strikes", {}).items() if int(v) > 0})
            for kind, rows in data.get("learned", {}).items():
                self._learned[str(kind)] = {str(n): [int(r[0]), float(r[1]), None if r[2] is None else float(r[2])]
                                            for n, r in rows.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            log.warning("router state file unreadable; starting fresh", exc_info=True)

    def _save_state(self) -> None:
        if not self._persisting():
            return
        now_wall, now = self._wall(), self._clock()
        data = {"cooldown": {n: round(now_wall + t - now, 1) for n, t in self._cooldown.items() if t > now},
                "demoted": {n: round(now_wall + t - now, 1) for n, t in self._demoted.items() if t > now},
                "strikes": dict(self._strikes), "learned": self._learned}
        try:
            atomic_write_text(self._state_path, json.dumps(data, sort_keys=True), mode=0o600)
        except OSError:
            log.warning("could not save the router state", exc_info=True)

    # ---- learned routing table ------------------------------------------------------------------------------
    def _learn(self, kind: str, name: str, ok: bool, first_token_ms: float | None) -> None:
        row = self._learned.setdefault(kind, {}).setdefault(name, [0, 1.0, None])
        calls, success, ms = row
        weight = 1.0 if calls == 0 else LEARN_ALPHA
        success += weight * ((1.0 if ok else 0.0) - success)
        if ok and first_token_ms is not None:
            ms = first_token_ms if ms is None else ms + LEARN_ALPHA * (first_token_ms - ms)
        row[:] = [calls + 1, round(success, 4), None if ms is None else round(ms, 1)]
        if self._wall() - self._learn_saved >= LEARN_SAVE_S:
            self._learn_saved = self._wall()
            self._save_state()

    def learned(self) -> dict:
        return {kind: {name: {"n": r[0], "success": r[1], "ms": r[2]} for name, r in rows.items()}
                for kind, rows in self._learned.items()}

    def _learned_rank(self, kind: str, name: str) -> int | None:
        """Expected time to a good answer, in coarse log steps (so small differences never reorder); None when
        too little is known."""
        row = self._learned.get(kind, {}).get(name)
        if not self._store.config.router.learned or row is None or row[0] < LEARN_MIN:
            return None
        _calls, success, ms = row
        expected = (ms if ms is not None else 10000.0) / max(success, 0.05)
        return round(2 * math.log2(expected + 50))

    # ---- health -------------------------------------------------------------------------------------------
    def _window(self, name: str) -> list[tuple[float, bool, float | None]]:
        cutoff = self._clock() - HEALTH_TTL_S
        return [r for r in self._health.get(name, ()) if r[0] >= cutoff]

    def _record(self, name: str, ok: bool, first_token_ms: float | None = None) -> None:
        window = self._health.setdefault(name, deque(maxlen=HEALTH_WINDOW))
        now = self._clock()
        if ok and name in self._demoted:  # a probe after demotion worked: start afresh
            window.clear()
            del self._demoted[name]
        window.append((now, ok, first_token_ms))
        recent = self._window(name)
        if not ok and len(recent) >= HEALTH_MIN_CALLS and not any(r[1] for r in recent):
            self._demoted[name] = now + DEMOTE_S
            log.warning("provider %s demoted for %.0fs: no success in its last %d calls", name, DEMOTE_S, len(recent))
            self._save_state()

    def _is_demoted(self, name: str) -> bool:
        until = self._demoted.get(name)
        if until is None:
            return False
        if until > self._clock():
            return True
        self._health.pop(name, None)  # time is up: the next call is a fresh probe
        del self._demoted[name]
        return False

    def _band(self, name: str) -> int:
        """0 healthy (or not enough calls to say), 1 shaky, 2 mostly failing."""
        recent = self._window(name)
        if len(recent) < HEALTH_MIN_CALLS:
            return 0
        rate = sum(1 for r in recent if r[1]) / len(recent)
        return 0 if rate >= 0.8 else 1 if rate >= 0.5 else 2

    def health(self, name: str) -> dict:
        recent = self._window(name)
        oks = sorted(r[2] for r in recent if r[1] and r[2] is not None)
        return {
            "calls": len(recent), "ok": sum(1 for r in recent if r[1]),
            "success_rate": round(sum(1 for r in recent if r[1]) / len(recent), 2) if recent else None,
            "p50_ms": oks[len(oks) // 2] if oks else None,
            "demoted_for": max(0.0, round(self._demoted.get(name, 0.0) - self._clock(), 1)),
        }

    def frugal_level(self) -> str:
        cfg = self._store.config.frugal
        if cfg.level == "F0" and cfg.auto_in_brownout:
            try:
                return "F1" if self.capacity_low() else "F0"
            except Exception:  # noqa: BLE001
                return "F0"
        return cfg.level

    def _sized(self, cfg: ProviderConfig, size: str, tier: str, frugal: dict | None = None) -> ProviderConfig:
        """The provider config to call for this turn: its small model for simple fast turns, when it has one,
        and the frugal-mode request fields when it understands them."""
        if cfg.kind == "claude_cli":
            return self._claude_sized(cfg, size, tier)
        small = cfg.effective_small_model()
        if size == "small" and small and tier == "fast" and small != cfg.model:
            cfg = cfg.model_copy(update={"model": small})
        if frugal and cfg.effective_reasoning_support():
            cfg = cfg.model_copy(update={"extra": {**cfg.extra, **frugal}})
        return cfg

    def _claude_sized(self, cfg: ProviderConfig, size: str, tier: str) -> ProviderConfig:
        """The model and effort for this Claude call: the one you asked for ("use opus on high effort"), else the
        ladder step for this turn's difficulty (fast turns), else the provider's own (thinking mode)."""
        if self.claude_pick is not None:
            model, effort = self.claude_pick
        else:
            step = (cfg.extra.get("plan") or {}).get(size) if tier == "fast" else None
            model, effort = (step[0], step[1]) if step and len(step) == 2 else (cfg.model, cfg.extra.get("effort"))
        return cfg.model_copy(update={"model": model, "extra": {**cfg.extra, "effort": effort or ""}})

    def has_claude(self) -> bool:
        return any(c.kind == "claude_cli" for c in self.usable())

    def arm_claude(self, pick) -> None:
        """Use this model and effort for the next request only (it lapses after ten minutes)."""
        self._armed = (pick, self._clock() + 600.0)

    def take_armed_claude(self):
        armed, self._armed = self._armed, None
        return armed[0] if armed and armed[1] > self._clock() else None

    def _candidates(self, tools: list[ToolSpec], sensitive: bool, tier: str = "fast",
                    prefer: str | None = None, *, size: str = "large", est_tokens: int = 0,
                    capability: str | None = None, kind: str = "",
                    frugal: dict | None = None) -> list[tuple[ProviderConfig, str]]:
        rcfg = self._store.config.router
        # the user's spoken pick leads ordinary answers; thinking mode keeps its deep models first
        first = prefer or (self._store.config.preferred_provider if tier == "fast" else "")
        out = []
        for cfg in self._store.config.providers:
            if not cfg.enabled or (tools and not cfg.supports_tools) or (
                    sensitive and not cfg.effective_policy()["allow_sensitive"]):
                continue
            key = self._key(cfg)
            if cfg.requires_key and not key or not self._present(cfg):
                continue
            if self._cooldown.get(cfg.name, 0.0) > self._clock():
                continue
            if cfg.is_paid() and not self._paid_ok(cfg, capability):
                continue  # paid only for an opted-in capability, within this month's cap
            if tier == "fast" and cfg.tier == "deep" and not rcfg.allow_deep_fallback and cfg.name != first:
                continue  # a 180 s thinking model is no fallback for a spoken answer
            out.append((self._sized(cfg, size, tier, frugal), key or ""))
        if not rcfg.health_order:
            ordered = [c for c in out if c[0].tier == tier] + [c for c in out if c[0].tier != tier]
            return [c for c in ordered if c[0].name == first] + [c for c in ordered if c[0].name != first]
        position = {c[0].name: i for i, c in enumerate(out)}
        headroom = {c[0].name: self._headroom(c[0], est_tokens) for c in out}
        learned = {c[0].name: self._learned_rank(kind, c[0].name) for c in out}
        known = sorted(v for v in learned.values() if v is not None)
        neutral = known[len(known) // 2] if known else 0  # providers not learned yet sit in the middle

        def rank(c):
            name = c[0].name
            demoted = self._is_demoted(name)
            broke, low = headroom[name]
            lr = learned[name] if learned[name] is not None else neutral
            return (broke, demoted, not (name == first and not demoted and not low), c[0].tier != tier, low,
                    self._band(name), lr, position[name])

        return sorted(out, key=rank)

    def _spent_usd(self, cfg: ProviderConfig) -> float | None:
        if not cfg.price_per_mtok_usd or self.ledger is None:
            return None
        return round(self.ledger.month_tokens(cfg.name, cfg.model) / 1e6 * cfg.price_per_mtok_usd, 2)

    def _paid_ok(self, cfg: ProviderConfig, capability: str | None) -> bool:
        if not capability or capability not in cfg.capabilities or cfg.monthly_cap_usd <= 0:
            return False
        try:
            spent = self._spent_usd(cfg)
        except Exception:  # noqa: BLE001 — a spend that cannot be checked is not spent
            log.warning("could not check the spend of %s", cfg.name, exc_info=True)
            return False
        return spent is not None and spent < cfg.monthly_cap_usd

    def _headroom(self, cfg: ProviderConfig, est_tokens: int) -> tuple[bool, bool]:
        """(cannot afford this call, short of today's capacity) from the ledger; (False, False) when unknown."""
        rcfg = self._store.config.router
        if self.ledger is None or not rcfg.usage_order or not rcfg.ledger:
            return False, False
        try:
            if not self.ledger.can_afford(cfg, est_tokens):
                return True, True
            share = self.ledger.remaining(cfg)["share"]
        except Exception:  # noqa: BLE001 — a broken ledger must not stop routing
            log.warning("capacity ledger unreadable", exc_info=True)
            return False, False
        return False, share is not None and share < rcfg.low_headroom

    def _mark(self, name: str, err: LLMError) -> None:
        self._last_error[name] = str(err)
        self._last_kind[name] = err.kind
        if err.kind in REQUEST_SHAPED and self._store.config.router.classify_errors:
            log.info("provider %s rejected the request (%s); not cooling it: %s", name, err.kind, err)
            return
        strikes = self._strikes[name] = self._strikes.get(name, 0) + 1
        if err.retry_after is not None:
            seconds = min(err.retry_after, 3600.0)
        else:
            base = {429: 60.0, 401: 3600.0, 403: 3600.0}.get(err.status, 30.0)
            seconds = min(base * 2 ** (strikes - 1), max(base, MAX_BACKOFF_S))
        self._cooldown[name] = self._clock() + seconds
        log.warning("provider %s cooling down %.0fs: %s", name, seconds, err)
        self._save_state()

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec], *, sensitive: bool = False, tier: str = "fast",
        prefer: str | None = None, request_errors: str = "failover", capability: str | None = None,
        size: str | None = None,
    ) -> AsyncIterator[Event]:
        """request_errors="raise": a request-shaped error (too large, tool not offered) is raised at once, with
        .provider set, so the caller can repair the request and retry that provider instead of failing over."""
        rcfg = self._store.config.router
        hedged = rcfg.hedge and tier == "fast"
        size = size or (classify_level(messages) if rcfg.complexity and tier == "fast" else "large")
        level = self.frugal_level()
        annotate_turn(complexity=size, frugal=level)
        request_fields = frugal_params(level, size, tier)
        if not request_fields and rcfg.fast_reasoning and tier == "fast" and size == "small":
            request_fields = {"reasoning_effort": rcfg.fast_reasoning, "include_reasoning": False}  # fast turns, less deliberation
        inner = (self._stream_hedged if hedged else self._stream_sequential)(
            messages, tools, sensitive, tier, prefer, request_errors, size, capability, request_fields)
        async with contextlib.aclosing(inner) as events:
            async for event in events:
                yield event

    # ---- what leaves the machine --------------------------------------------------------------------------
    def _open(self, cfg: ProviderConfig, provider: Provider, messages: list[Message], tools: list[ToolSpec]):
        """The provider's event stream; a provider that may train on prompts gets placeholders for names, e-mails,
        phone numbers and addresses, and its reply is mapped back here."""
        dbg = self._store.config.debug
        if dbg.inject:  # failure injection (off by default): fail where a real provider's error would surface
            self._faults.configure(dbg.seed)
            kind = self._faults.pick(dbg.inject, cfg.name, dbg.only)
            if kind is not None:
                return failing_stream(FaultInjector.error(kind, cfg.name), dbg.slow_s)
        if not self._store.config.router.pseudonymize or not cfg.effective_policy()["trains_on_data"]:
            return provider.stream(messages, tools)
        return self._masked(provider, messages, tools)

    async def _masked(self, provider: Provider, messages: list[Message], tools: list[ToolSpec]):
        p = Pseudonymizer(names=self._store.config.safety.pseudonym_names)
        out = StreamUnmasker(p)
        async with contextlib.aclosing(provider.stream(p.mask_messages(messages), tools)) as events:
            async for event in events:
                if isinstance(event, TextDelta):
                    text = out.feed(event.text)
                    if text:
                        yield TextDelta(text)
                elif isinstance(event, ToolCallEvent):
                    tail = out.flush()
                    if tail:
                        yield TextDelta(tail)
                    yield ToolCallEvent(dataclasses.replace(event.call, arguments=p.unmask_args(event.call.arguments)))
                else:
                    yield event
        tail = out.flush()
        if tail:
            yield TextDelta(tail)

    # ---- shared bookkeeping for one provider call ----------------------------------------------------------
    def _failed(self, cfg: ProviderConfig, e: LLMError) -> None:
        self._mark(cfg.name, e)
        if e.kind not in REQUEST_SHAPED:
            self._record(cfg.name, False)
        e.provider = cfg.name

    def _succeeded(self, cfg: ProviderConfig, started: float, first: float | None) -> None:
        had = self._strikes.pop(cfg.name, None) is not None or cfg.name in self._demoted
        self._record(cfg.name, True, round((first - started) * 1000, 1) if first else None)
        if had:
            self._save_state()

    def _trace_call(self, cfg: ProviderConfig, tier: str, sensitive: bool, tools: list[ToolSpec], chars_in: int, *,
                    chars_out: int, calls: int, outcome: str, started: float, first: float | None,
                    error: LLMError | None, hedged: bool = False, rate=None, kind: str = "",
                    usage: Usage | None = None) -> None:
        schema = sum(len(t.name) + len(t.description) + len(json.dumps(t.parameters)) for t in tools)
        if usage is None:
            usage, source = Usage(tokens_in=(chars_in + schema) // 4, tokens_out=chars_out // 4), "estimate"
        else:
            source = "provider"
        self._account(cfg, outcome, error, rate, usage)
        if kind and outcome == "ok":
            self._learn(kind, cfg.name, True, round((first - started) * 1000, 1) if first else None)
        elif kind and outcome == "error" and error is not None and error.kind not in REQUEST_SHAPED:
            self._learn(kind, cfg.name, False, None)
        extra = {"hedged": True} if hedged else {}
        self.tracer.event("llm_call", **{
            **turn_fields(), "provider": cfg.name, "model": cfg.model, "tier": tier, "sensitive": sensitive,
            "tools": len(tools), "tool_names": [t.name for t in tools], "chars_in": chars_in,
            "chars_out": chars_out, "tool_calls": calls, "outcome": outcome,
            "first_token_ms": round((first - started) * 1000, 1) if first else None,
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
            "status": getattr(error, "status", None), "error_kind": getattr(error, "kind", None),
            "error": str(error)[:200] if error else None, "tokens_in": usage.tokens_in, "tokens_out": usage.tokens_out,
            "reasoning_tokens": usage.reasoning, "cached_tokens": usage.cached, "usage_source": source, **extra})

    def _account(self, cfg: ProviderConfig, outcome: str, error: LLMError | None, rate, usage: Usage) -> None:
        """Count a call the provider served or at least accepted (refusals and unreachable hosts cost nothing),
        with the tokens it reported, or about 4 characters a token (tool schemas included) when it reported none."""
        if self.ledger is None or not self._store.config.router.ledger:
            return
        if error is not None and (error.kind in ("rate_limit", "too_large", "auth")
                                  or (error.kind == "server" and error.status is None)):
            return
        try:
            self.ledger.record(cfg.name, cfg.model, usage.tokens_in, usage.tokens_out,
                               rate=rate if rate is not None else getattr(error, "rate", None))
        except Exception:  # noqa: BLE001 — bookkeeping must never break an answer
            log.warning("could not record usage for %s", cfg.name, exc_info=True)

    async def ask_one(self, name: str, messages: list[Message], timeout_s: float = 20.0) -> tuple[str, float]:
        """One short question to exactly this provider (no fail-over, no tools): (the text, seconds taken).
        Counted in the capacity ledger like any call. For checks such as the daily canary."""
        cfg = next((c for c in self.usable() if c.name == name), None)
        if cfg is None:
            raise LLMError(f"{name}: not available")
        provider = self._factory(cfg, self._key(cfg) or "")
        started = time.perf_counter()
        parts: list[str] = []

        async def collect() -> None:
            async with contextlib.aclosing(provider.stream(messages, [])) as events:
                async for event in events:
                    if isinstance(event, TextDelta):
                        parts.append(event.text)

        try:
            await asyncio.wait_for(collect(), timeout_s)
        except asyncio.TimeoutError as e:
            raise llm_error(f"{name}: no answer within {timeout_s:g} s") from e
        text = "".join(parts)
        self._account(cfg, "ok", None, getattr(provider, "last_rate", None),
                      getattr(provider, "last_usage", None) or Usage(sum(len(m.content) for m in messages) // 4,
                                                                      len(text) // 4))
        return text, time.perf_counter() - started

    @staticmethod
    def _exhausted(out_of_time: bool, deadline: float, last: LLMError | None) -> LLMError:
        why = f" (last error: {last})" if last else ""
        if out_of_time:
            return DeadlineExceeded(f"no model answered within {deadline:g} s{why}")
        return LLMError("no LLM provider available" + why)

    # ---- one provider at a time ----------------------------------------------------------------------------
    @staticmethod
    def _kind(size: str, tier: str, tools: list[ToolSpec]) -> str:
        """The kind of request, for the learned table: small/large (or the tier), with or without tools."""
        return (size if tier == "fast" else tier) + ("+tools" if tools else "")

    @staticmethod
    def _estimate(messages: list[Message], tools: list[ToolSpec]) -> int:
        """Tokens a request will cost, at about 4 characters each, tool schemas included."""
        chars = sum(len(m.content or "") for m in messages)
        chars += sum(len(t.name) + len(t.description) + len(json.dumps(t.parameters)) for t in tools)
        return chars // 4

    async def _stream_sequential(self, messages, tools, sensitive, tier, prefer, request_errors, size="large",
                                 capability=None, frugal=None):
        last: LLMError | None = None
        chars_in = sum(len(m.content or "") for m in messages)
        # fast answers must start within the turn's deadline, across every provider tried; thinking mode may wait
        deadline = self._store.config.turn.first_token_deadline_s if tier == "fast" else 0.0
        t_start, out_of_time = time.monotonic(), False
        kind = self._kind(size, tier, tools)
        for cfg, key in self._candidates(tools, sensitive, tier, prefer, size=size,
                                         est_tokens=self._estimate(messages, tools), capability=capability,
                                         kind=kind, frugal=frugal):
            if deadline and time.monotonic() - t_start >= deadline:
                out_of_time = True
                break
            provider = self._factory(cfg, key)
            emitted = False
            started, first = time.perf_counter(), None
            chars_out = calls = 0
            outcome, error = "cancelled", None  # stays "cancelled" if the consumer stops reading early
            try:
                async with contextlib.aclosing(self._open(cfg, provider, messages, tools)) as provider_events:
                    events = aiter(provider_events)
                    while True:
                        budget = None
                        try:
                            if emitted or not deadline:
                                event = await anext(events)
                            else:
                                async with asyncio.timeout(deadline - (time.monotonic() - t_start)) as budget:
                                    event = await anext(events)
                        except StopAsyncIteration:
                            break
                        except TimeoutError as e:
                            if budget is None or not budget.expired():
                                raise LLMError(f"{cfg.name}: timed out", kind="timeout") from e
                            out_of_time = True
                            raise LLMError(f"{cfg.name}: no answer within the {deadline:g} s deadline",
                                           kind="timeout") from e
                        if not emitted:
                            self.active = (cfg.name, cfg.model)
                            first = time.perf_counter()
                        emitted = True
                        if isinstance(event, TextDelta):
                            chars_out += len(event.text)
                        elif isinstance(event, ToolCallEvent):
                            calls += 1
                        yield event
                outcome = "ok"
                self._succeeded(cfg, started, first)
                return
            except LLMError as e:
                outcome, error = "error", e
                self._failed(cfg, e)
                last = e
                if emitted or (request_errors == "raise" and e.kind in REQUEST_SHAPED):
                    raise
            finally:
                self._trace_call(cfg, tier, sensitive, tools, chars_in, chars_out=chars_out, calls=calls,
                                 outcome=outcome, started=started, first=first, error=error,
                                 rate=getattr(provider, "last_rate", None), kind=kind,
                                 usage=getattr(provider, "last_usage", None))
        raise self._exhausted(out_of_time, deadline, last)

    # ---- hedged: a second provider joins when the first is slow -------------------------------------------
    async def _stream_hedged(self, messages, tools, sensitive, tier, prefer, request_errors, size="large",
                             capability=None, frugal=None):
        """The first provider gets hedge_after_s to produce a token; then the next one starts too. The first to
        produce anything wins and the other is cancelled. Errors before a token fail over as usual."""
        chars_in = sum(len(m.content or "") for m in messages)
        deadline = self._store.config.turn.first_token_deadline_s
        hedge_after = self._store.config.router.hedge_after_s
        t_start = time.monotonic()
        kind = self._kind(size, tier, tools)
        queue = iter(self._candidates(tools, sensitive, tier, prefer, size=size,
                                      est_tokens=self._estimate(messages, tools), capability=capability, kind=kind,
                                      frugal=frugal))
        racers: list[_Racer] = []
        last: LLMError | None = None
        hedge_used, winner = False, None

        def launch(hedged: bool = False) -> bool:
            nxt = next(queue, None)
            if nxt is not None:
                provider = self._factory(nxt[0], nxt[1])
                racers.append(_Racer(provider, nxt[0], self._open(nxt[0], provider, messages, tools), hedged))
            return nxt is not None

        def trace(r: "_Racer", outcome: str, error: LLMError | None = None) -> None:
            r.traced = True
            self._trace_call(r.cfg, tier, sensitive, tools, chars_in, chars_out=r.chars_out, calls=r.calls,
                             outcome=outcome, started=r.started, first=r.first, error=error, hedged=r.hedged,
                             rate=getattr(r.provider, "last_rate", None), kind=kind,
                             usage=getattr(r.provider, "last_usage", None))

        try:
            launch()
            while winner is None:
                live = [r for r in racers if not r.out]
                if not live:
                    if (deadline and time.monotonic() - t_start >= deadline) or not launch():
                        raise self._exhausted(bool(deadline and time.monotonic() - t_start >= deadline),
                                              deadline, last)
                    continue
                now = time.monotonic()
                waits = []
                if deadline:
                    waits.append(deadline - (now - t_start))
                if not hedge_used and len(live) == 1 and hedge_after > 0:
                    waits.append(live[0].mono + hedge_after - now)
                timeout = max(0.0, min(waits)) if waits else None
                waiters = {asyncio.ensure_future(r.ready.wait()): r for r in live}
                done, pending = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                for w in pending:
                    w.cancel()
                if not done:
                    if deadline and time.monotonic() - t_start >= deadline:
                        for r in live:
                            e = LLMError(f"{r.cfg.name}: no answer within the {deadline:g} s deadline", kind="timeout")
                            self._failed(r.cfg, e)
                            r.out, last = True, e
                            trace(r, "error", e)
                        raise self._exhausted(True, deadline, last)
                    hedge_used = True
                    launch(hedged=True)
                    continue
                for r in [r for r in live if r.ready.is_set()]:
                    kind, item = r.head()
                    if kind == "error":
                        self._failed(r.cfg, item)
                        r.out, last = True, item
                        trace(r, "error", item)
                        if request_errors == "raise" and item.kind in REQUEST_SHAPED:
                            raise item
                    elif winner is None:
                        winner = r
            for r in racers:
                if r is not winner and not r.out:
                    r.cancel()
                    trace(r, "cancelled")
            self.active = (winner.cfg.name, winner.cfg.model)
            emitted = False
            while True:
                kind, item = winner.take() if winner.pending() else await winner.next()
                if kind == "event":
                    emitted = True
                    if isinstance(item, TextDelta):
                        winner.chars_out += len(item.text)
                    elif isinstance(item, ToolCallEvent):
                        winner.calls += 1
                    yield item
                elif kind == "done":
                    self._succeeded(winner.cfg, winner.started, winner.first)
                    trace(winner, "ok")
                    return
                else:
                    self._failed(winner.cfg, item)
                    trace(winner, "error", item)
                    raise item
        finally:
            for r in racers:
                r.cancel()
            await asyncio.gather(*(r.task for r in racers), return_exceptions=True)
            for r in racers:
                if not r.traced:
                    trace(r, "cancelled")

    # ---- start-up key probe and the health board ------------------------------------------------------------
    async def warm(self, client: httpx.AsyncClient | None = None) -> int:
        """Open the connection to each provider's address now (`router.warm_on_wake`), so the first real request does not pay for
        the handshake. One bodiless HEAD per address, no key sent, nothing counted against any limit. Returns how many answered."""
        client = client or get_client()
        urls = []
        for cfg in self.usable():
            if cfg.kind == "claude_cli":
                continue
            url = (cfg.base_url or ("https://api.anthropic.com" if cfg.kind == "anthropic" else "")).rstrip("/")
            if url and url not in urls:
                urls.append(url)

        async def one(url: str) -> bool:
            try:
                await client.head(url, timeout=httpx.Timeout(3.0))
                return True
            except httpx.HTTPError:
                return False

        return sum(await asyncio.gather(*(one(u) for u in urls))) if urls else 0

    async def probe(self, client: httpx.AsyncClient | None = None) -> dict[str, str]:
        """One model-list request per provider (no tokens used) so expired or revoked keys show up before they
        are needed. A rejected key rests the provider for an hour; unreachable is only reported."""
        client = client or get_client()
        targets: dict[tuple[str, str], list[str]] = {}
        local = {c.name: "ok" for c in self.usable() if c.kind == "claude_cli"}  # no address to ask: it is a program
        for cfg in self.usable():
            if cfg.kind == "claude_cli":
                continue
            key = self._key(cfg) or ""
            if cfg.kind == "anthropic":
                url = (cfg.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/models"  # as AnthropicProvider
            else:
                url = cfg.base_url.rstrip("/") + "/models"
            targets.setdefault((url, key), []).append(cfg.name)

        async def one(url: str, key: str, kind: str) -> str:
            headers = ({"x-api-key": key, "anthropic-version": "2023-06-01"} if kind == "anthropic"
                       else {"Authorization": f"Bearer {key}"} if key else {})
            try:
                resp = await client.get(url, headers=headers, timeout=httpx.Timeout(5.0))
            except httpx.HTTPError:
                return "unreachable"
            if resp.status_code in (401, 403):
                return "key rejected"
            return "ok" if resp.status_code < 400 else f"HTTP {resp.status_code}"

        kinds = {c.name: c.kind for c in self.usable()}
        found = await asyncio.gather(*(one(url, key, kinds[names[0]]) for (url, key), names in targets.items()))
        results: dict[str, str] = dict(local)
        for ((_url, _key), names), result in zip(targets.items(), found):
            for name in names:
                results[name] = result
                if result == "key rejected":
                    self._mark(name, LLMError(f"{name}: key rejected by the start-up check", status=401))
        self._probe.update(results)
        return results

    def board(self) -> list[dict]:
        """One row per provider: its state in a word, recent success rate and typical first-token time."""
        rows = []
        for row in self.status():
            health = row["health"]
            if not row["enabled"]:
                state = "off"
            elif not row["has_key"]:
                state = "no key"
            elif row.get("probe") == "key rejected":
                state = "key rejected"
            elif row["cooling_for"] > 0:
                state = "resting"
            elif health["demoted_for"] > 0:
                state = "demoted"
            elif row.get("probe") == "unreachable" and not health["ok"]:
                state = "unreachable"
            else:
                state = "ok"
            rows.append({"name": row["name"], "state": state, "success_rate": health["success_rate"],
                         "p50_ms": health["p50_ms"]})
        return rows

    def status(self) -> list[dict]:
        now = self._clock()
        return [
            {
                "name": cfg.name,
                "enabled": cfg.enabled,
                "tier": cfg.tier,
                "has_key": bool(self._key(cfg)) or not cfg.requires_key,
                "cooling_for": max(0.0, round(self._cooldown.get(cfg.name, 0.0) - now, 1)),
                "last_error": self._last_error.get(cfg.name),
                "last_error_kind": self._last_kind.get(cfg.name),
                "health": self.health(cfg.name),
                "probe": self._probe.get(cfg.name),
                "privacy": cfg.privacy_label(),
                "paid": cfg.is_paid(),
                **({"spent_usd": self._spent_usd(cfg), "monthly_cap_usd": cfg.monthly_cap_usd}
                   if cfg.is_paid() else {}),
            }
            for cfg in self._store.config.providers
        ]


class _Racer:
    """One provider call running in its own task, buffering what it produces for the hedged router."""

    def __init__(self, provider: Provider, cfg: ProviderConfig, opened, hedged: bool):
        self.cfg, self.hedged, self.provider = cfg, hedged, provider
        self.started, self.mono, self.first = time.perf_counter(), time.monotonic(), None
        self.chars_out = self.calls = 0
        self.out = self.traced = False  # out: failed before winning (or timed out)
        self.ready = asyncio.Event()  # something to read
        self._items: deque = deque()
        self.task = asyncio.ensure_future(self._run(opened))

    async def _run(self, opened) -> None:
        try:
            async with contextlib.aclosing(opened) as events:
                async for event in events:
                    if self.first is None:
                        self.first = time.perf_counter()
                    self._push(("event", event))
            self._push(("done", None))
        except LLMError as e:
            self._push(("error", e))
        except Exception as e:  # noqa: BLE001 — a broken provider is a failed call, never a crashed turn
            self._push(("error", LLMError(f"{self.cfg.name}: {e}", kind="other")))

    def _push(self, item) -> None:
        self._items.append(item)
        self.ready.set()

    def head(self):
        """The first thing produced, without consuming it."""
        return self._items[0]

    def pending(self) -> bool:
        return bool(self._items)

    def take(self):
        item = self._items.popleft()
        if not self._items:
            self.ready.clear()
        return item

    async def next(self):
        await self.ready.wait()
        return self.take()

    def cancel(self) -> None:
        if not self.task.done():
            self.task.cancel()
