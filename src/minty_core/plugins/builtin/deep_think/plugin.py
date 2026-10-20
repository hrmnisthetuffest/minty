import asyncio
import contextlib
import logging
import re

from minty_core.jobs import short, two_sentences
from minty_core.llm.types import Message, TextDelta
from minty_core.plugins.host import Tool

log = logging.getLogger(__name__)
DEFAULT_TIMEOUT_S = 120.0
MAX_TIMEOUT_S = 900.0
_BLOCK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.S | re.I)
_CLOSE = re.compile(r"</think(?:ing)?>", re.I)
_OPEN = re.compile(r"<think(?:ing)?>", re.I)
SYSTEM = ("You are a careful expert advisor. Reason thoroughly, then give only the final answer as plain spoken "
          "sentences, under 150 words, no markdown and no lists.")


def _strip_thinking(text: str) -> str:
    text = _BLOCK.sub("", text)
    closes = list(_CLOSE.finditer(text))
    if closes:  # a stray closing tag (closing-only or nested output): the answer follows the last one
        text = text[closes[-1].end():]
    return _OPEN.split(text, maxsplit=1)[0].strip()  # an unfinished block runs to the end


def _timeout(settings: dict) -> float:
    raw = settings.get("timeout_s")
    if raw in (None, ""):
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0
    if not 0 < value <= MAX_TIMEOUT_S:
        log.warning("deep_think: ignoring timeout_s=%r (need 0 < seconds <= %g); using %g",
                    raw, MAX_TIMEOUT_S, DEFAULT_TIMEOUT_S)
        return DEFAULT_TIMEOUT_S
    return value


def register(ctx):
    async def answer(question: str, context: str) -> str:
        router = ctx.services.get("router")
        if router is None:
            return "Deep thinking is unavailable."
        prompt = question if not context else f"{question}\n\nContext:\n{context}"
        parts: list[str] = []

        async def collect():
            is_private = ctx.services.get("is_private")
            stream = router.stream([Message("system", SYSTEM), Message("user", prompt)], [], tier="deep",
                                   sensitive=bool(is_private and is_private()))
            async with contextlib.aclosing(stream) as events:
                async for event in events:
                    if isinstance(event, TextDelta):
                        parts.append(event.text)

        try:
            await asyncio.wait_for(collect(), _timeout(ctx.settings))
        except asyncio.TimeoutError:
            return "I thought about it for too long and gave up. Try asking something more specific."
        return _strip_thinking("".join(parts)) or "I couldn't reach a conclusion."

    def question_of(args) -> tuple[str, str]:
        question = str(args.get("question") or "").strip()
        if not question:
            raise ValueError("empty question")
        return question, str(args.get("context") or "").strip()

    async def think_deeply(args):
        return await answer(*question_of(args))

    async def think_in_background(args):
        question, context = question_of(args)
        quick_only = ctx.services.get("quick_only")
        if quick_only is not None and quick_only():
            return "The user asked for quick answers only; do not think further."
        runner = ctx.services.get("jobs")
        if runner is None:
            return await answer(question, context)
        is_private = ctx.services.get("is_private")

        def announce(job):
            if job.status != "done":
                return None
            return f"Update on {short(question, 60)}: {two_sentences(job.result, 400)}"

        async def work(job_ctx):
            job_ctx.progress("thinking it through")
            return await answer(question, context)

        runner.submit("think", question, work, budget_s=_timeout(ctx.settings) + 30,
                      private=bool(is_private and is_private()), announce=announce)
        return "Started. Tell the user you'll follow up once you've thought it through."

    schema = {"type": "object", "properties": {"question": {"type": "string"}, "context": {"type": "string"}},
              "required": ["question"]}
    return [
        Tool("think_deeply",
             "Ask a slower, smarter model a hard question (maths, planning, analysis) and wait. Say you're thinking; "
             "give it every needed fact.",
             schema, think_deeply),
        Tool("think_in_background",
             "After a short best-effort answer to a hard or high-stakes question: a smarter model checks it in the "
             "background and the result is spoken later. Never for simple questions.",
             schema, think_in_background),
    ]
