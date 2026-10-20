import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from minty_core.plugins.host import Tool
from minty_core.policy import R1

MAX_DAYS = 31
MAX_CHARS = 8000
MAX_ENTRY_LENGTH = 2000


def register(ctx):
    lock = threading.Lock()

    def folder() -> Path:
        configured = str(ctx.settings.get("directory") or "").strip()
        return Path(configured).expanduser() if configured else Path.home() / "Documents" / "Minty-Journal"

    def add_entry(args):
        text = " ".join(str(args["text"]).split())
        if not text:
            raise ValueError("empty entry")
        if len(text) > MAX_ENTRY_LENGTH:
            raise ValueError(f"entry too long (max {MAX_ENTRY_LENGTH} chars)")
        now = datetime.now()
        path = folder() / f"{now.date().isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with lock:
            with path.open("a", encoding="utf-8") as f:
                if f.tell() == 0:
                    f.write(f"# {now.date().isoformat()}\n\n")
                f.write(f"- {now:%H:%M} {text}\n")
        return "Saved to your journal."

    def read_entries(args):
        start = date.fromisoformat(args["start"]) if args.get("start") else date.today()
        end = date.fromisoformat(args["end"]) if args.get("end") else start
        if end < start or (end - start).days >= MAX_DAYS:
            raise ValueError(f"range must be 1 to {MAX_DAYS} days, start before end")
        parts = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            path = folder() / f"{day.isoformat()}.md"
            if path.exists():
                parts.append(path.read_text(encoding="utf-8").strip())
        if not parts:
            return f"No entries for {start.isoformat()}." if start == end else f"No entries from {start} to {end}."
        return "\n\n".join(parts)[-MAX_CHARS:]

    def search_entries(args):
        query = str(args["query"]).strip().lower()
        if not query:
            raise ValueError("empty query")
        hits = []
        for path in sorted(folder().glob("????-??-??.md")) if folder().exists() else []:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("- ") and query in line.lower():
                    hits.append(f"{path.stem}: {line[2:]}")
        return "\n".join(hits[:20]) or "No matches."

    def schema(**props):
        return {"type": "object", "properties": {k: {"type": "string"} for k in props}, "required": [k for k, req in props.items() if req]}

    return [
        Tool("add_entry", "Add a timestamped entry to the user's journal.", schema(text=True), add_entry, risk=R1),
        Tool("read_entries", "Read journal entries for a date or an ISO date range (start, end).",
             schema(start=False, end=False), read_entries, sensitive=True),
        Tool("search_entries", "Search all journal entries for a word or phrase.", schema(query=True),
             search_entries, sensitive=True),
    ]
