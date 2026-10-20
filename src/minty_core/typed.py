"""Typing to Minty in the terminal display: the same answer path as the local API (a command phrase is answered by the command
router, anything else by the assistant), shown on the display as a conversation. Typed chat gives a text answer; it does not
speak, and it waits while the voice is in the middle of something."""
from __future__ import annotations

import logging

from minty_core.commands import CommandRouter
from minty_core.llm.types import LLMError
from minty_core.trace import turn_scope

log = logging.getLogger(__name__)


def command_router(ctx) -> CommandRouter:
    """The command router wired to everything it may use (the API and typed chat share it)."""
    return CommandRouter(ctx.agent, ctx.store, prompts=ctx.services.get("prompts"), router=ctx.router,
                         jobs=ctx.services.get("jobs"), capacity=ctx.services.get("brownout"),
                         where=ctx.services.get("where"), receipt=ctx.services.get("receipt"),
                         lockdown=ctx.services.get("lockdown"), receipts=ctx.services.get("receipts"),
                         permissions=ctx.services.get("permissions"))


async def answer_text(ctx, text: str) -> str:
    """The reply to one typed line. Raises LLMError when no model could answer."""
    reply = command_router(ctx).handle(text)
    if reply is not None:
        return reply
    with turn_scope():
        sentences = [s async for s in ctx.agent.respond(text)]
    return " ".join(sentences)


async def typed_chat(ctx, text: str) -> None:
    """One typed line, from the display: show it, answer it, show the answer (or why there is none)."""
    text = text.strip()
    if not text:
        return
    pipeline = getattr(ctx, "pipeline", None)
    if pipeline is not None and getattr(pipeline, "state", "idle") not in ("idle", "loading"):
        ctx.bus.publish("notice", message="I'm in the middle of something; ask again in a moment.")
        return
    ctx.bus.publish("transcript", role="user", text=text)
    try:
        reply = await answer_text(ctx, text)
    except LLMError as e:
        ctx.bus.publish("error", message=f"No answer: {e}")
        return
    except Exception as e:  # noqa: BLE001 - a typed line must never take the display down
        log.exception("typed chat failed")
        ctx.bus.publish("error", message=f"Something went wrong: {type(e).__name__}")
        return
    if reply:
        ctx.bus.publish("transcript", role="assistant", text=reply)
