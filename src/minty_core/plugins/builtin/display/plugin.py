import webbrowser

from minty_core.plugins.host import Tool
from minty_core.policy import link_egress

OBJ = {"type": "object", "properties": {}}
MAX_CONTENT = 6000


def register(ctx):
    publish = ctx.services.get("publish", lambda *a, **k: None)

    def show(args):
        title = str(args.get("title") or "Minty")[:80]
        content = str(args.get("content") or "")[:MAX_CONTENT]
        if not content.strip():
            return "Error: nothing to show."
        publish("display", title=title, content=content)
        return "Shown on screen."

    def clear(args):
        publish("display", title="", content="")
        return "Screen cleared."

    async def open_url(args):
        from minty_core.netguard import check_public_url

        url = str(args.get("url") or "")
        try:
            await check_public_url(url)
        except ValueError as e:
            return f"Error: {e}"
        webbrowser.open(url)
        return f"Opened {url} in the browser."

    return [
        Tool("show", "Show results, lists or tables on the Minty screen (Markdown). Also say the key point aloud.",
             {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
              "required": ["content"]}, show),
        Tool("clear", "Clear the Minty screen.", OBJ, clear),
        Tool("open_url", "Open a web page in the user's browser.",
             {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, open_url,
             egress=link_egress()),
    ]
