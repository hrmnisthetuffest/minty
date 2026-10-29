"""Tell the user something on their own Telegram: one message to the paired chat, never to anyone else.

The chat is whatever the user paired from the terminal; the model cannot name another. Nothing goes out in private mode, and in quiet
hours it still goes, because the user asked for it just now (the notifier's own pushes keep the quiet hours)."""
import re

from minty_core.plugins.host import Tool
from minty_core.policy import Mandate, R2

MAX_CHARS = 3500
# the user's own words ask for a message on their phone; without them a message there is a question
MANDATE = Mandate(r"\b(telegram|(?:on|to) my phone)\b", max_class=R2)


def register(ctx):
    async def tell(args):
        text = re.sub(r"\n{3,}", "\n\n", str(args.get("text") or "")).strip()[:MAX_CHARS]
        if not text:
            return "What should I send you on Telegram?"
        services = ctx.services
        if services.get("is_private", lambda: False)() or ctx.services["config"]().safety.privacy_mode:
            return "I am in private mode, so I am not sending anything to Telegram."
        runtime = services.get("telegram")
        if runtime is None:
            return "Telegram is not running here. Turn it on (telegram.enabled), add the bot key (`minty set-key telegram`), pair your chat, then restart me."
        if not runtime.gateway.allowed:
            return "No Telegram chat is paired with me yet. Pair yours first: run `minty telegram-pair` and send the printed /pair code to your bot."
        if runtime.gateway.suspended:
            return "Telegram is paused (lockdown), so nothing can be sent."
        return "Sent to your Telegram." if await runtime.push.push(text, force=True) else "I could not send that to Telegram."

    return [Tool("tell", "Message the user on their own paired Telegram, when they ask (send me that on Telegram).",
                 {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, tell,
                 risk=R2, mandate=MANDATE, content_ok=lambda args: True)]  # into the user's own chat: reading something first is not a reason to ask
