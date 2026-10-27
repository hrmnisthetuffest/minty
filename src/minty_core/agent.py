import asyncio
import json
import time
import contextlib
import logging
import re
from dataclasses import replace
from typing import AsyncIterator, Awaitable, Callable

from minty_core.config import ConfigStore
from minty_core.plugins.host import PARALLEL_SAFE_TOOLS
from minty_core import foldkit
from minty_core.agentloop import StuckDetector, fingerprint, trim_old_observations
from minty_core.tasks import spawn
from minty_core.claimcheck import correction, unbacked_claims
from minty_core.toolargs import check_args
from minty_core.toolbreaker import ToolBreaker
from minty_core import promptlib
from minty_core.claude_pick import parse_pick
from minty_core.llm.types import REQUEST_SHAPED, LLMError, Message, TextDelta, ToolCall, ToolCallEvent, ToolNotOffered
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore, compile_prompt
from minty_core.prompts import section as prompt_section
from minty_core.policy import Guard, destinations_in
from minty_core.residency import LOCAL_STUB, LocalResult
from minty_core.secretscan import SecretScanner
from minty_core.text import SentenceSplitter
from minty_core.toolkits import CORE_TOOLS, NEED_TOOLKIT, ToolkitRouter
from minty_core.trace import annotate_turn, current_turn_id, transcript_hash
from minty_core.webpage import stub_old_pages

log = logging.getLogger(__name__)
FALLBACK_PERSONALITY = "You are Minty, a helpful voice assistant."
MAX_RESULT_CHARS = 6000  # a tool result longer than this is cut before it enters the conversation
SHRUNK_RESULT_CHARS = 2500  # per result, when a provider said the request was too large
SHRUNK_HISTORY = 8  # messages kept (from a user turn on) in that case


def shape_result(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… ({len(text) - limit} more characters not shown; ask for a narrower result)"


def _one_line(text: str, limit: int) -> str:
    flat = " ".join((text or "").split()).replace('"', "'")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def collapse_history(history: list[Message], keep: int) -> tuple[str, list[Message]]:
    """(note, recent): the last `keep` user turns stay as they are; each older turn becomes one line of the note
    (what was asked, which tools ran, what was answered). No model call. keep <= 0 changes nothing."""
    starts = [i for i, m in enumerate(history) if m.role == "user"]
    if keep <= 0 or len(starts) <= keep:
        return "", history
    cut = starts[-keep]
    lines = []
    for a, b in zip(starts, starts[1:] + [len(history)]):
        if a >= cut:
            break
        tools = sorted({c.name for m in history[a:b] for c in m.tool_calls})
        reply = next((m.content for m in reversed(history[a:b]) if m.role == "assistant" and m.content), "")
        line = f'- You asked: "{_one_line(history[a].content, 90)}"'
        if tools:
            line += f" → used {', '.join(tools)[:60]}"
        if reply:
            line += f' → replied: "{_one_line(reply, 90)}"'
        lines.append(line)
    note = "Earlier in this conversation (one line per turn):\n" + "\n".join(lines)
    if len(note) >= sum(len(m.content or "") for m in history[:cut]):
        return "", history  # short turns: the lines would cost more than the turns themselves
    return note, history[cut:]


def _is_chat_message(m: Message) -> bool:
    """What a person sees in the chat: what they sent and what was answered (not tool plumbing)."""
    return m.role == "user" or (m.role == "assistant" and bool(m.content))


def _chat_turn(turn: list[Message]) -> bool:
    """A turn that belongs to a Telegram chat and is not private (private turns follow the short voice window)."""
    return turn[0].chat and not any(m.private for m in turn)


def _turn_line(history: list[Message], a: int, b: int) -> str:
    tools = sorted({c.name for m in history[a:b] for c in m.tool_calls})
    reply = next((m.content for m in reversed(history[a:b]) if m.role == "assistant" and m.content), "")
    line = f'- You asked: "{_one_line(history[a].content, 90)}"'
    if tools:
        line += f" → used {', '.join(tools)[:60]}"
    if reply:
        line += f' → replied: "{_one_line(reply, 90)}"'
    return line


SUPER_ITEM_CHARS = 36
SUPER_MAX_CHARS = 1500


def _very_short(history: list[Message], a: int, b: int) -> str:
    tools = sorted({c.name for m in history[a:b] for c in m.tool_calls})
    return _one_line(history[a].content, SUPER_ITEM_CHARS) + (f" [{tools[0]}]" if tools else "")


def tier_history(history: list[Message], full: int, compressed: int) -> tuple[str, list[Message]]:
    """(note, recent) for a long chat, counting messages back from the newest (no model call):
    the newest `full` messages stay as they are; turns that began up to `compressed` messages ago become one line
    each (what was asked, tools used, what was answered); older turns shrink to a few words of what was asked."""
    starts = [i for i, m in enumerate(history) if m.role == "user"]
    if not starts:
        return "", history
    after = [0] * (len(history) + 1)  # chat messages from here to the newest, counted from the end
    for i in range(len(history) - 1, -1, -1):
        after[i] = after[i + 1] + _is_chat_message(history[i])
    cut = next((a for a in starts if after[a] <= full), len(history))
    older = [a for a in starts if a < cut]
    if not older:
        return "", history
    ends = dict(zip(starts, starts[1:] + [len(history)]))
    lines = [_turn_line(history, a, ends[a]) for a in older if after[a] <= compressed]
    items = [_very_short(history, a, ends[a]) for a in older if after[a] > compressed]
    parts = []
    if items:
        kept, size = [], 0
        for item in reversed(items):  # the newest of the old ones first; the oldest are the ones that fall off
            if size + len(item) + 2 > SUPER_MAX_CHARS:
                break
            kept.append(item)
            size += len(item) + 2
        left_out = "(older ones left out) " if len(kept) < len(items) else ""
        parts.append("Long ago in this chat, a few words each (oldest first): " + left_out + "; ".join(reversed(kept)))
    if lines:
        parts.append("Earlier in this conversation (one line per turn):\n" + "\n".join(lines))
    note = "\n\n".join(parts)
    if len(note) >= sum(len(m.content or "") for m in history[:cut]):
        return "", history  # short turns: the lines would cost more than the turns themselves
    return note, history[cut:]


_SECRET_KEY = re.compile(r"key|token|secret|password", re.IGNORECASE)
_PROMPT_MAX = 160


def _confirmation_prompt(tool, call: ToolCall) -> str:
    """The spoken question before a risky tool runs: the tool's own wording, else its name and arguments."""
    describe = getattr(tool, "describe", None)
    if describe is not None:
        try:
            described = describe(call.arguments)
            if isinstance(described, str) and described.strip():
                return described.strip()
        except Exception:
            log.warning("describe() for %s failed; using the generic prompt", call.name, exc_info=True)
    parts = []
    for key, value in (call.arguments if isinstance(call.arguments, dict) else {}).items():
        shown = "…" if _SECRET_KEY.search(str(key)) else " ".join(str(value).split())
        parts.append(f"{key} {shown}")
    prompt = f"Should I run {call.name.replace('_', ' ')}" + (f" with {', '.join(parts)}?" if parts else "?")
    return prompt if len(prompt) <= _PROMPT_MAX else prompt[: _PROMPT_MAX - 1] + "…"


class Agent:
    def __init__(
        self,
        router,
        host,
        store: ConfigStore,
        personalities: PersonalityStore,
        memory: MemoryStore,
        confirm: Callable[[str], Awaitable[bool]],
        *,
        max_rounds: int = 5,
        max_history: int = 20,
        tier: str = "fast",
        private_probe: Callable[[], bool] | None = None,
        archive=None,
    ):
        self._router = router
        self._host = host
        self._store = store
        self._personalities = personalities
        self._memory = memory
        self.confirm = confirm
        self._max_rounds = max_rounds
        self._max_history = max_history
        self.tier = tier
        self.history: list[Message] = []
        self._chat_turn, self._turn_start = False, 0
        self.force_private: bool = False
        self.quick_only: bool = False  # "just the quick answer": no background thinking for this conversation
        self.private_probe = private_probe  # live "is the user in a private conversation?" check, asked per round
        self._lock = asyncio.Lock()
        self.archive = archive  # ConversationArchive or None
        self.prompt_extras: list[Callable[[], str]] = []  # extra system-prompt sections (history, learned rules)
        # sections that depend on this request (a matched skill); they go last so the front stays byte-stable
        self.turn_extras: list[Callable[[str], str]] = []
        self.toolkits: ToolkitRouter | None = None  # set by the app: which tools this request is offered
        self.skills = None  # skills.SkillIndex or None: a matched skill switches on the tool groups it names
        self._skill_memo: tuple[str | None, object] = (None, None)
        self.turn_done: list[Callable[[str, str], None]] = []  # (request, reply) after every finished answer
        self.on_tool: Callable[[str | None], None] | None = None  # tool name while one runs, then None
        self.tracer = getattr(router, "tracer", None)
        self.breaker = ToolBreaker()  # a tool that keeps failing is not offered for a few minutes
        self.guard = None  # policy.Guard; set by the app (with the audit log) or made on first use
        self.safety = None  # safety.SafetyKit: checks that run before the gate and can only block
        self.provenance = None  # provenance.Provenance: what untrusted content this request read
        self.receipts = None  # receipts.ReceiptStore: every change, with why
        self._fold_summary = ""  # the older conversation, summarised (foldkit.py); in memory only
        self._fold_pending: list[list[Message]] = []  # turns that fell out of the window and are not summarised yet
        self._fold_task: asyncio.Task | None = None
        self.last_fast: tuple[str, dict] | None = None  # the quick command the newest request ran without a model, if any
        self._changed: list[str] = []  # what this turn has already changed (a send, a delete), for an interrupted answer
        self.on_reset: list[Callable[[], None]] = []  # called when the conversation is started over
        # (text) -> a fixed reply or None: asked before any model call (the coach's safety gate); a reply ends the turn
        self.input_gates: list[Callable[[str], str | None]] = []
        self.offline = None  # offline.OfflineHandler: local answers when there is no internet
        # request -> FastAction for simple page commands ("click Store"), or None: run with no model call (fastpath.py)
        self.fastpath: Callable[[str], object] | None = None
        # request -> instant.Hit or None: a question the computer can answer by itself (the time, a sum): no model call
        self.local_answer: Callable[[str], object] | None = None
        self.terse_prompts: Callable[[], bool] = lambda: False  # frugal mode: the terse fixed sections (if gated in)
        self.reply_started = False  # this reply's model has begun to answer (the pipeline skips "one moment" then)

    def _system_prompt(self, request: str = "") -> str:
        cfg = self._store.config
        try:
            base = self._personalities.get(cfg.personality.active)
        except (FileNotFoundError, ValueError):
            base = FALLBACK_PERSONALITY
        lean = cfg.turn.lean_prompt
        base_rules = prompt_section("BASE_RULES", self.terse_prompts(), lean)
        library = ""
        if cfg.personality.prompt_library:
            off = cfg.personality.prompt_modules_off
            channel = "telegram" if self._chat_turn else "voice"
            if channel == "telegram" and "telegram_rules" in promptlib.GATES and "telegram_rules" not in off:
                base_rules = promptlib.TELEGRAM_RULES
            try:
                kits = tuple(self.toolkits.select(request)) if self.toolkits is not None and request else ()
            except Exception:  # noqa: BLE001 — a failed tool-group guess only means fewer modules
                kits = ()
            modules = promptlib.select(channel=channel, tier=self.tier, kits=kits, off=off)
            library = promptlib.render(modules)
            annotate_turn(prompt_modules=[m.name for m in modules], prompt_version=promptlib.version(modules, base_rules))
        notes = self._memory.text()
        if cfg.turn.stable_prefix:  # everything that stays the same first; the notes, which change, after it
            prompt = compile_prompt(base, cfg.personality, "", base_rules=base_rules) + library
            if notes.strip():
                prompt += "\n\nThings you remember about the user:\n" + notes.strip()
        else:
            prompt = compile_prompt(base, cfg.personality, notes, base_rules=base_rules) + library
        extras = []
        for provider in self.prompt_extras:
            try:
                extras.append(provider())
            except Exception:
                log.warning("a prompt section failed and was skipped", exc_info=True)
        for provider in self.turn_extras:
            try:
                extras.append(provider(request))
            except Exception:
                log.warning("a request-specific prompt section failed and was skipped", exc_info=True)
        live = getattr(self._host, "prompt_context", None)  # e.g. the open web page; changes every turn, so last
        if live is not None:
            try:
                block = live()
            except Exception:
                log.warning("the live prompt context failed and was skipped", exc_info=True)
                block = ""
            if block:
                extras.append("\n\n" + block)
        return prompt + "".join(e for e in extras if e)

    def _outgoing(self, request: str) -> list[Message]:
        """What one model call sends: the system prompt and the conversation (on a diet, when that is on)."""
        turn = self._store.config.turn
        history = stub_old_pages(self.history) if turn.request_diet else self.history
        if any(m.chat for m in history):  # a Telegram chat: tiers by message count, not the few-turns diet
            note, history = tier_history(history, turn.chat_full_messages,
                                         max(turn.chat_compressed_messages, turn.chat_full_messages))
        else:
            note, history = collapse_history(history, turn.keep_full_turns)
        broken = self.breaker.note()
        folded = self._fold_note() if turn.fold_history else ""
        note = "\n\n".join(x for x in (folded, note) if x)
        return [Message("system", self._system_prompt(request) + ("\n\n" + note if note else "")
                        + ("\n\n" + broken if broken else "")), *history]

    def _tool_specs(self, request: str):
        resting = self.breaker.resting()  # tools that keep failing are not offered for a while
        specs = [s for s in self._host.tool_specs() if s.name not in resting]
        if self.toolkits is None or not self._store.config.toolkit_routing:
            return specs
        skill = self._matched_skill(request)
        offered = self.toolkits.tool_names(request, self._skill_toolkits(skill), strict=skill is not None)
        annotate_turn(toolkits=list(getattr(self.toolkits, "last_selected", [])))
        return [s for s in specs if s.name in offered] + [self.toolkits.spec()]

    def _matched_skill(self, request: str):
        """The skill for this request; matched once and reused by the prompt and the tool choice."""
        if self.skills is None:
            return None
        if self._skill_memo[0] != request:
            self._skill_memo = (request, self.skills.match(request))
        return self._skill_memo[1]

    def _skill_toolkits(self, skill) -> tuple[str, ...]:
        if skill is None or self.toolkits is None or not skill.allowed_tools:
            return ()
        wanted = set(skill.allowed_tools)
        return tuple(kit for kit, tools in self.toolkits.kits().items() if wanted & set(tools))

    def _tool_state(self, name: str | None) -> None:
        if self.on_tool is not None:
            try:
                self.on_tool(name)
            except Exception:
                log.warning("tool state hook failed", exc_info=True)

    def _after_turn(self, request: str, reply: str) -> None:
        for hook in self.turn_done:
            try:
                hook(request, reply)
            except Exception:
                log.warning("a turn hook failed", exc_info=True)

    def reset_conversation(self) -> int:
        """Start fresh now. The conversation is already in the archive; it moves to 'earlier today'."""
        count = len(self.history)
        self.history.clear()
        self._fold_summary, self._fold_pending = "", []
        for hook in self.on_reset:
            try:
                hook()
            except Exception:  # noqa: BLE001
                log.warning("a reset hook failed", exc_info=True)
        self.quick_only = False
        if self.toolkits is not None:
            self.toolkits.reset()
        if self.archive is not None:
            self.archive.mark_reset()
        return count

    def _age_out(self) -> None:
        """Drop conversation older than the memory window; the archive still has it (and summarises it later).
        A Telegram chat that is not private is kept for turn.chat_keep_hours instead."""
        now, turn = time.time(), self._store.config.turn
        voice_cutoff = now - self._store.config.memory.window_min * 60
        chat_cutoff = now - turn.chat_keep_hours * 3600

        def expired(t: list[Message]) -> bool:
            return t[0].role != "user" or t[0].ts < (chat_cutoff if _chat_turn(t) else voice_cutoff)

        self._keep_turns(lambda turns: [t for t in turns if not expired(t)])
        if not self.history:  # the conversation is over: nothing summarised belongs to a new one
            self._fold_summary, self._fold_pending = "", []

    def _keep_turns(self, choose, fold: bool = False) -> None:
        """Rewrite the history turn by turn (a turn is a user message and what followed it). With `fold`, the turns this drops
        are handed to the summariser when that is switched on."""
        turns: list[list[Message]] = []
        for m in self.history:
            if m.role == "user" or not turns:
                turns.append([])
            turns[-1].append(m)
        kept = choose(turns)
        if len(kept) != len(turns):
            keep_ids = {id(t) for t in kept}
            self.history[:] = [m for t in kept for m in t]
            if fold and self._store.config.turn.fold_history:
                self._schedule_fold([t for t in turns if id(t) not in keep_ids and t and t[0].role == "user"])

    def _schedule_fold(self, dropped: list[list[Message]]) -> None:
        """Turns left the window: summarise them in the background once enough have, never holding up an answer."""
        if not dropped:
            return
        self._fold_pending += dropped
        if len(self._fold_pending) >= self._store.config.turn.fold_every_turns and (
                self._fold_task is None or self._fold_task.done()):
            self._fold_task = spawn(self._run_fold(), "fold history")

    async def _run_fold(self) -> None:
        cfg = self._store.config
        while len(self._fold_pending) >= cfg.turn.fold_every_turns:
            batch, self._fold_pending = self._fold_pending, []
            scanner = getattr(self.safety, "scanner", None) or SecretScanner()  # masking never switches itself off
            redact = scanner.redact
            sensitive = foldkit.private_in(batch) or self.force_private or cfg.safety.privacy_mode
            messages = foldkit.request(self._fold_summary, foldkit.chat_lines(batch, redact), cfg.turn.fold_max_chars)
            try:
                text = []
                stream = self._stream(messages, [], sensitive, prefer=None, raise_request_errors=False)
                async with contextlib.aclosing(stream) as events:
                    async for event in events:
                        if isinstance(event, TextDelta):
                            text.append(event.text)
                summary = foldkit.tidy("".join(text), cfg.turn.fold_max_chars)
                if summary:
                    self._fold_summary = summary
            except Exception:  # noqa: BLE001 — a summary that fails costs only the summary; the conversation goes on
                log.warning("could not summarise the older conversation", exc_info=True)

    async def wait_for_fold(self) -> None:
        while self._fold_task is not None and not self._fold_task.done():
            await asyncio.shield(self._fold_task)

    def _fold_note(self) -> str:
        """The older conversation as notes: the summary, then one line for each turn not summarised yet."""
        parts = []
        if self._fold_summary:
            parts.append(f"{foldkit.NOTE_HEAD}\n{self._fold_summary}")
        if self._fold_pending:
            parts.append("Just before that, one line per turn:\n" + "\n".join(
                _turn_line(t, 0, len(t)) for t in self._fold_pending[-8:]))
        return "\n\n".join(parts)

    def _mark_chat(self) -> None:
        if self._chat_turn:
            for m in self.history[self._turn_start:]:
                m.chat = m.chat or not m.private

    def _trim(self) -> None:
        """The voice window keeps `max_history` messages; a Telegram chat keeps up to turn.chat_keep_messages besides."""
        self._mark_chat()
        self._age_out()
        cap = self._store.config.turn.chat_keep_messages

        def newest(turns):
            chat = [t for t in turns if _chat_turn(t)]
            voice = [t for t in turns if not _chat_turn(t)]
            for group, limit in ((chat, cap), (voice, self._max_history)):
                while group and sum(len(t) for t in group) > limit:
                    group.pop(0)
            keep = {id(t) for t in chat + voice}
            return [t for t in turns if id(t) in keep]

        self._keep_turns(newest, fold=True)

    def _log(self, role: str, text: str, private: bool = False) -> None:
        if self.archive is not None:
            try:
                self.archive.log(role, text, private)
            except Exception:
                log.warning("could not write to the conversation archive", exc_info=True)

    def _probe_private(self) -> bool:
        if self.private_probe is None:
            return False
        try:
            return bool(self.private_probe())
        except Exception:
            log.warning("private_probe failed; treating the conversation as not private", exc_info=True)
            return False

    async def _run_call(self, call: ToolCall) -> tuple[str, bool]:
        if call.name == NEED_TOOLKIT and self.toolkits is not None:
            asked = call.arguments.get("name") if isinstance(call.arguments, dict) else ""
            wanted_for = next((m.content for m in reversed(self.history) if m.role == "user"), "")
            return self.toolkits.need(str(asked or ""), wanted_for), False
        tool = self._host.get_tool(call.name)
        approval_id = None
        if tool is not None:  # a call that cannot work never reaches the safety gate, the user or the tool
            call.arguments, problem = check_args(tool, call.arguments)
            if problem:
                return problem, False
        if tool is not None and self.safety is not None:
            blocked = self.safety.precheck(call.name, call.arguments, tool)
            if blocked:
                self._receipt(tool, call, "blocked", detail=blocked)
                return f"Blocked by a safety check: {blocked}.", False
        if tool is not None:
            known, prompt, extra = self._gate_inputs(tool, call)
            if self.safety is not None:
                advice = await self.safety.advise(call.name, call.arguments)
                if advice:
                    prompt = f"{prompt} {advice}"
            decision = await self.guard.authorize(tool, call.name, call.arguments, self.confirm,
                                                  prompt=prompt, extra_risk=extra, known=known)
            approval_id = decision.approval_id
            if not decision.allowed:
                blocked_by_rule = decision.reason == "blocked by a safety rule" or not decision.asked
                self._receipt(tool, call, "blocked" if blocked_by_rule else "declined", approval_id,
                              detail=decision.reason)
                return ("Blocked by the user's safety rules." if decision.reason == "blocked by a safety rule"
                        else "The user declined."), False
        standing_note = decision.reason if tool is not None and decision.allowed and \
            decision.reason.startswith("standing approval") else ""
        if tool is not None and self.safety is not None:
            self.safety.executing(call.name, call.arguments, tool)
        self._tool_state(call.name)
        try:
            raw = await self._host.call(call.name, call.arguments)
        finally:
            self._tool_state(None)
        return self._finish_call(tool, call, raw, approval_id, standing_note)

    def _finish_call(self, tool, call: ToolCall, raw, approval_id: int | None = None,
                     standing_note: str = "") -> tuple[str, bool]:
        """Everything that happens once a tool has answered: the result is cut to size, counted, remembered as read, and a
        receipt is written. Shared by one call at a time and by a batch of lookups (which run this in the order asked)."""
        result = shape_result(raw)
        if self.toolkits is not None:
            self.toolkits.used(call.name)
        self.breaker.record(call.name, result.startswith("Error"))
        if self.provenance is not None:
            self.provenance.note_result(call.name, result)
        if tool is not None:
            failed = result.startswith("Error")
            self._receipt(tool, call, "failed" if failed else "ok", approval_id,
                          detail=result[:160] if failed else standing_note)
            if not failed and self._changes_something(tool, call):
                self._changed.append(self._what(call))
        if tool is not None and tool.local_only and not result.startswith("Error"):
            return LocalResult(result), True
        return result, bool(tool is not None and tool.sensitive)

    def _gate_inputs(self, tool, call: ToolCall) -> tuple[set, str, int]:
        """What the safety gate is shown for this call: destinations the user mentioned, the question, any extra risk."""
        if self.guard is None:
            self.guard = Guard(self._store, tracer=self.tracer)
        known = set().union(*(destinations_in(m.content) for m in self.history if m.role == "user"))
        prompt, extra = _confirmation_prompt(tool, call), 0
        if self.provenance is not None:
            mine = " ".join(m.content for m in self.history if m.role == "user")
            note = self.provenance.note_for(call.arguments, mine, call.name)
            if note:
                prompt = f"{prompt} {note}."
                extra = self.provenance.extra_risk(call.arguments, mine, call.name)
        return known, prompt, extra

    def _gate_for_batch(self, call: ToolCall):
        """(tool, assessment) when this call may run alongside others: an audited lookup that changes nothing and passes every check
        without asking anyone. Anything else (a change, a question, a block, a malformed call) goes the usual way, one at a
        time, so approvals are never asked together and nothing a change depends on is still running."""
        if call.name not in PARALLEL_SAFE_TOOLS or not self._store.config.turn.parallel_reads:
            return None
        tool = self._host.get_tool(call.name)
        if tool is None:
            return None
        args, problem = check_args(tool, call.arguments)
        if problem or self._changes_something(tool, ToolCall(call.id, call.name, args)):
            return None
        call.arguments = args
        if self.safety is not None and self.safety.precheck(call.name, call.arguments, tool):
            return None
        known, prompt, extra = self._gate_inputs(tool, call)
        assessed = self.guard.assess(tool, call.name, call.arguments, prompt, extra_risk=extra, known=known)
        if assessed.decision != "allow" or assessed.risk != 0:
            return None
        return tool, assessed

    async def _run_reads(self, batch: list[tuple]) -> list[tuple[str, bool]]:
        """Lookups that passed the gate, run at the same time; answered and noted in the order they were asked."""
        for (tool, assessed), call in batch:
            self.guard.settle(assessed)  # recorded exactly as it is for a single call
            if self.safety is not None:
                self.safety.executing(call.name, call.arguments, tool)
        self._tool_state(batch[0][1].name)  # once around the whole batch, not flickering as each one ends
        try:
            raws = await asyncio.gather(*(self._host.call(c.name, c.arguments) for _, c in batch), return_exceptions=True)
        finally:
            self._tool_state(None)
        for raw in raws:
            if isinstance(raw, BaseException):  # as when they ran one at a time: the turn ends and rolls back
                raise raw
        return [self._finish_call(gate[0], call, raw) for (gate, call), raw in zip(batch, raws)]

    @staticmethod
    def _what(call: ToolCall) -> str:
        args = call.arguments if isinstance(call.arguments, dict) else {}
        shown = ", ".join(f"{k} {str(v)[:40]}" for k, v in list(args.items())[:3])
        return call.name.replace("_", " ") + (f" with {shown}" if shown else "")

    @staticmethod
    def _changes_something(tool, call: ToolCall) -> bool:
        from minty_core.policy import classify
        return classify(tool, call.arguments if isinstance(call.arguments, dict) else {}) >= 1

    def _receipt(self, tool, call: ToolCall, outcome: str, approval_id: int | None = None, detail: str = "") -> None:
        """A receipt for anything that changes something (class 1 and up); reads leave none."""
        if self.receipts is None:
            return
        try:
            from minty_core.policy import classify
            risk = classify(tool, call.arguments if isinstance(call.arguments, dict) else {})
            if risk < 1:
                return
            summary = self._what(call)
            request = next((m.content for m in reversed(self.history) if m.role == "user"), "")
            self.receipts.record(call.name, risk, summary, request=" ".join(request.split()), approval_id=approval_id,
                                 outcome=outcome, detail=detail, turn_id=current_turn_id() or "")
        except Exception:  # noqa: BLE001 — a receipt failing must never stop the action or hide its result
            log.warning("could not write a receipt", exc_info=True)

    async def offline_reply(self, user_text: str) -> str | None:
        """After a model call failed: if the network itself is down, the local answer (or the honest no); None when
        the network is fine and the models are the problem."""
        if self.offline is None or not self._store.config.offline.enabled:
            return None
        self.offline.probe.note_failure()
        try:
            if await self.offline.probe.online(refresh=True):
                return None
            return await self.offline.answer(user_text)
        except Exception:  # noqa: BLE001 — the fallback must not make a failure worse
            log.warning("offline fallback failed", exc_info=True)
            return None

    async def _fast(self, user_text: str) -> str | None:
        """Run a simple page command without the model. Returns what to say, or None to let the model answer
        (also after a failed attempt, which stays in the history so the model sees it)."""
        if self.fastpath is None:
            return None
        try:
            action = self.fastpath(user_text)
        except Exception:
            log.warning("fast path matching failed; asking the model", exc_info=True)
            return None
        if action is None:
            return None
        call = ToolCall(f"fast_{int(time.time() * 1000) % 10**8}", action.tool, action.args)
        result, private = await self._run_call(call)  # the same Guard/policy path as a model's tool call
        self.history.append(Message("assistant", "", tool_calls=[call]))
        self.history.append(Message("tool", result, tool_call_id=call.id, name=call.name, private=private))
        failed = result.startswith("Error")
        if not failed:
            self.last_fast = (action.tool, dict(action.args))
        if self.tracer is not None:
            self.tracer.event("fastpath", tool=action.tool, outcome="fallback" if failed else "ok")
        if failed:
            return None
        if result == "The user declined.":
            reply = "Okay, I left it."
        elif result.startswith("Blocked by"):
            reply = "Your safety rules block that."
        else:
            reply = action.say(result) if callable(action.say) else action.say
        self.history.append(Message("assistant", reply))
        self._log("assistant", reply)
        self._trim()
        self._after_turn(user_text, reply)
        return reply

    def _instant(self, user_text: str, untrusted: str) -> str | None:
        """Answer from the instant rules (the time, a sum, a conversion): nothing is sent anywhere and no model is asked. Text
        that came from outside is never answered this way."""
        if self.local_answer is None or untrusted:
            return None
        try:
            hit = self.local_answer(user_text)
        except Exception:  # noqa: BLE001 - a broken rule must not cost the user an answer
            log.warning("instant answer failed; asking the model", exc_info=True)
            return None
        if hit is None:
            return None
        self.history.append(Message("assistant", hit.reply))
        self._log("assistant", hit.reply)
        if self.tracer is not None:
            self.tracer.event("instant", rule=hit.rule, category=hit.category)
        self._trim()
        self._after_turn(user_text, hit.reply)
        return hit.reply

    def _can_recover(self) -> bool:
        return bool(getattr(self._router, "supports_request_errors", False)
                    and self._store.config.router.recover_request_errors)

    def _stream(self, messages, specs, sensitive: bool, *, prefer: str | None, raise_request_errors: bool):
        kwargs: dict = {"sensitive": sensitive, "tier": self.tier}
        if prefer:
            kwargs["prefer"] = prefer
        if raise_request_errors:
            kwargs["request_errors"] = "raise"
        return self._router.stream(messages, specs, **kwargs)

    def _repair(self, err: LLMError, request: str, messages: list[Message], specs: list):
        """Fix a request a provider rejected for its shape; returns (messages, specs, provider to retry)."""
        before = (sum(len(m.content or "") for m in messages), len(specs))
        note: dict = {}
        prefer = err.provider or None
        if isinstance(err, ToolNotOffered):
            kit = self.toolkits.kit_of(err.tool) if self.toolkits is not None and err.tool else None
            note = {"tool": err.tool, "kit": kit}
            if kit is not None and self.toolkits.load(kit):
                specs = self._tool_specs(request)
            else:
                prefer = None  # nothing to load: let another provider try
        else:
            messages, specs = self._shrink(messages, specs)
        if self.tracer is not None:
            self.tracer.event("agent_recovery", error_kind=err.kind, provider=err.provider, **note,
                              chars_before=before[0], chars_after=sum(len(m.content or "") for m in messages),
                              tools_before=before[1], tools_after=len(specs))
        log.info("provider %s rejected the request (%s); retrying repaired", err.provider or "?", err.kind)
        return messages, specs, prefer

    def _shrink(self, messages: list[Message], specs: list) -> tuple[list[Message], list]:
        """A smaller version of a request that was too large: older readings stubbed, results cut, fewer turns,
        and only the core tools plus the toolkits this conversation has used."""
        system, rest = messages[0], trim_old_observations(messages[1:], keep=1)
        rest = [replace(m, content=shape_result(m.content, SHRUNK_RESULT_CHARS)) if m.role == "tool" else m
                for m in rest]
        last_user = max((i for i, m in enumerate(rest) if m.role == "user"), default=0)
        cut = next((i for i in range(max(0, len(rest) - SHRUNK_HISTORY), len(rest)) if rest[i].role == "user"),
                   last_user)
        rest = rest[min(cut, last_user):]
        if self.toolkits is not None:
            called = {c.name for m in rest for c in m.tool_calls}
            kits = {self.toolkits.kit_of(n) for n in called} - {None}
            keep = set(CORE_TOOLS) | {NEED_TOOLKIT} | called
            keep |= {t for kit, tools in self.toolkits.kits().items() if kit in kits for t in tools}
            specs = [s for s in specs if s.name in keep]
        return [system, *rest], specs

    async def respond(self, user_text: str, *, sensitive: bool = False,
                      untrusted: str = "", chat: bool = False) -> AsyncIterator[str]:
        """One request. "use opus on high effort ..." names the Claude model and effort for this request, or, said
        alone, for the next one (claude_pick.py); everything else is _respond."""
        router = self._router
        pick, rest = parse_pick(user_text)
        if pick is not None:
            if not getattr(router, "has_claude", lambda: False)():
                yield "Claude isn't switched on. Run: minty claude-brain on, then restart me."
                return
            if not rest:
                router.arm_claude(pick)
                yield f"{pick.model.capitalize()}{f' on {pick.effort} effort' if pick.effort else ''} for your next request."
                return
            user_text = rest
        elif hasattr(router, "take_armed_claude"):
            pick = router.take_armed_claude()
        if pick is not None:
            router.claude_pick = pick
        try:
            async with contextlib.aclosing(
                    self._respond(user_text, sensitive=sensitive, untrusted=untrusted, chat=chat)) as sentences:
                async for sentence in sentences:  # closing this closes that, so an interrupted answer still rolls back
                    yield sentence
        finally:
            if pick is not None:
                router.claude_pick = None

    async def _respond(self, user_text: str, *, sensitive: bool = False,
                       untrusted: str = "", chat: bool = False) -> AsyncIterator[str]:
        """untrusted: text that came from outside (a forwarded message) and sits inside user_text as quoted data; the
        safety layer treats it like a page that was read."""
        async with self._lock:
            self._age_out()
            self.reply_started = False
            for gate in self.input_gates:
                try:
                    fixed = gate(user_text)
                except Exception:
                    log.warning("an input gate failed; carrying on without it", exc_info=True)
                    fixed = None
                if fixed:
                    self.history.append(Message("user", user_text, private=True))
                    self.history.append(Message("assistant", fixed, private=True))
                    self._log("user", user_text, True)
                    self._log("assistant", fixed, True)
                    self._trim()
                    yield fixed
                    return
            start = len(self.history)
            self._changed = []
            self.last_fast = None
            self._chat_turn, self._turn_start = chat, start  # a Telegram chat turn is kept longer (_mark_chat)
            turn_private = False
            self.history.append(Message("user", user_text))
            if self.provenance is not None:
                self.provenance.reset()  # what the last request read does not follow into this one
                if untrusted:
                    self.provenance.note_result("telegram_forward", untrusted)
            self._log("user", user_text)
            annotate_turn(transcript_hash=transcript_hash(user_text))
            if self.toolkits is not None:
                self.toolkits.next_turn()
            try:
                fast = await self._fast(user_text)
                if fast is not None:
                    yield fast
                    return
                instant = self._instant(user_text, untrusted)
                if instant is not None:
                    yield instant
                    return
                if (self.offline is not None and self._store.config.offline.enabled
                        and self.offline.probe.known_offline()):
                    reply = await self.offline.answer(user_text)  # no waiting on providers that cannot be reached
                    self.history.append(Message("assistant", reply))
                    self._log("assistant", reply)
                    self._trim()
                    self._after_turn(user_text, reply)
                    yield reply
                    return
                detector, trips = StuckDetector(), 0  # a model going round in circles: warned once, then stopped
                for _ in range(self._max_rounds):
                    sensitive = (sensitive or self.force_private or self._store.config.safety.privacy_mode
                                 or self._probe_private()
                                 or any(m.private for m in self.history))
                    annotate_turn(private=sensitive)  # e.g. the answer cache keeps nothing from a private turn
                    messages = self._outgoing(user_text)
                    specs = self._tool_specs(user_text)
                    splitter = SentenceSplitter(self._store.config.turn.first_clause_words)
                    parts: list[str] = []
                    calls: list[ToolCall] = []
                    recover = self._can_recover()
                    prefer = None
                    while True:
                        stream = self._stream(messages, specs, sensitive, prefer=prefer, raise_request_errors=recover)
                        try:
                            async with contextlib.aclosing(stream) as events:
                                async for event in events:
                                    if isinstance(event, TextDelta):
                                        self.reply_started = True
                                        parts.append(event.text)
                                        for sentence in splitter.feed(event.text):
                                            yield sentence
                                    elif isinstance(event, ToolCallEvent):
                                        calls.append(event.call)
                            break
                        except LLMError as e:
                            if not recover or parts or calls or e.kind not in REQUEST_SHAPED:
                                raise
                            recover = False  # one repair per round; after that the router fails over as before
                            messages, specs, prefer = self._repair(e, user_text, messages, specs)
                    tail = splitter.flush()
                    if tail:
                        yield tail
                    fix = ""
                    if not calls and self._store.config.turn.check_claims:
                        kinds = unbacked_claims("".join(parts), self._changed)
                        fix = correction(kinds) if kinds else ""
                    said = "".join(parts) + (f" {fix}" if fix else "")
                    self.history.append(Message("assistant", said, tool_calls=calls, private=turn_private))
                    self._log("assistant", said, turn_private or sensitive)
                    if not calls:
                        if fix:  # already spoken, so it cannot be taken back: say what really happened
                            yield fix
                        self._trim()
                        self._after_turn(user_text, said)
                        return
                    local_texts: list[str] = []
                    stopped = ""
                    i = 0
                    while i < len(calls):
                        if stopped:  # every call the model made gets an answer, so the history stays well formed
                            self.history.append(Message("tool", "Not run: the request was stopped to break a loop.",
                                                        tool_call_id=calls[i].id, name=calls[i].name, private=turn_private))
                            i += 1
                            continue
                        batch = []  # consecutive lookups that may run together (checked against the state as of now)
                        while i + len(batch) < len(calls) and (gate := self._gate_for_batch(calls[i + len(batch)])) is not None:
                            batch.append((gate, calls[i + len(batch)]))
                        if len(batch) >= 2:  # one lookup on its own takes the usual path, exactly as before
                            outcomes = await self._run_reads(batch)
                            group = [c for _, c in batch]
                        else:
                            group, outcomes = [calls[i]], [await self._run_call(calls[i])]
                        i += len(group)
                        for call, (result, private) in zip(group, outcomes):
                            sensitive = sensitive or private
                            turn_private = turn_private or private
                            if isinstance(result, LocalResult):  # for the user only: shown, never kept or sent on
                                local_texts.append(str(result))
                                result = LOCAL_STUB
                            why = detector.observe(
                                f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}",
                                fingerprint(str(result)), failed=str(result).startswith("Error"))
                            if why:
                                trips += 1
                                if trips == 1:
                                    result = (f"{result}\n[Do not repeat this: {why}. Try something different, "
                                              "or tell the user what is blocking you.]")
                                else:
                                    stopped = why
                            self.history.append(
                                Message("tool", result, tool_call_id=call.id, name=call.name, private=turn_private)
                            )
                    if stopped:
                        note = f"I'm going in circles on this, so I stopped: {stopped}. Tell me how you'd like to go on."
                        self.history.append(Message("assistant", note, private=turn_private))
                        self._log("assistant", note, turn_private)
                        self._trim()
                        yield note
                        return
                    if local_texts:
                        yield "\n".join(local_texts)
                        self.history.append(Message("assistant", LOCAL_STUB, private=True))
                        self._log("assistant", LOCAL_STUB, True)
                        self._trim()
                        self._after_turn(user_text, LOCAL_STUB)
                        return
                yield "I couldn't finish that request."
                self._trim()
            except BaseException:
                done = list(self._changed)
                del self.history[start:]
                if done:  # the answer is gone but the changes are not: the next turn must know they happened
                    self.history.append(Message("user", user_text, private=turn_private))
                    self.history.append(Message(
                        "assistant", "(That answer was cut off. Before it stopped I had already done: "
                        + "; ".join(done[:5]) + ".)", private=turn_private))
                raise
