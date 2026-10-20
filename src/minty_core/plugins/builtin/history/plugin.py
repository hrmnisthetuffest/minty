import re

from minty_core.plugins.host import Tool

OBJ = {"type": "object", "properties": {}}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def register(ctx):
    archive = ctx.services.get("archive")

    def list_days(args):
        days = archive.raw_days() if archive else []
        return ", ".join(days[-31:]) if days else "No past conversations are stored yet."

    def recall(args):
        day = str(args.get("date") or "")
        if archive is None or not _DATE.fullmatch(day):
            return "Error: give the date as YYYY-MM-DD."
        text = archive.raw_day(day)
        return text or f"No conversation was stored for {day}."

    def search(args):
        hits = archive.search_raw(str(args.get("query") or "")) if archive else []
        return "\n".join(hits) if hits else "Nothing found."

    return [
        Tool("list_days", "List the days with a stored full transcript. Only when the user explicitly asks for the "
                          "original conversation.", OBJ, list_days, sensitive=True),
        Tool("recall_day", "Read one past day's full word-for-word transcript. Only when the user explicitly asks for "
                           "the original conversation.",
             {"type": "object", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
              "required": ["date"]}, recall, sensitive=True),
        Tool("search", "Search all stored full transcripts for a word or phrase. Only when the user explicitly asks "
                       "for the original conversation.",
             {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
             search, sensitive=True),
    ]
