"""The notes vault as tools. Reading returns tagged data (never instructions); what came from outside is marked
untrusted; anything that changes a note is at least class 1 and never deletes. Off until `vault.enabled`."""
import re
from datetime import date

from minty_core.plugins.host import Tool
from minty_core.vault.kit import VaultKit, VaultOff, VaultProvider
from minty_core.vault.search import TAG, TAG_UNTRUSTED
from minty_core.vault.store import VaultError
from minty_core.vault.bookmarks import BookmarkError
from minty_core.vault.private import PrivateError
from minty_core.vault.decisions import weigh

MAX_OPEN = 6000
WRITE_FOLDERS = ("inbox", "notes", "areas", "projects", "people")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def register(ctx):
    provider = ctx.services.get("vault")
    if provider is None:
        provider = VaultProvider(config=ctx.services["config"], gists=lambda: ctx.services.get("gists"))
        ctx.services["vault"] = provider

    def kit() -> VaultKit:
        try:
            return provider.kit()
        except VaultOff as e:
            raise ValueError(str(e)) from None

    def guarded(fn):
        def run(args):
            try:
                return fn(args)
            except (VaultError, BookmarkError, PrivateError) as e:
                return str(e)
        return run

    def origin() -> str:
        """Who wrote this: 'agent', or 'web' when this request read something from outside (untrusted)."""
        prov = ctx.services.get("provenance")
        return "web" if prov is not None and prov.sources else "agent"

    def day_arg(args, key):
        v = str(args.get(key) or "").strip()
        return v if _DAY.match(v) else None

    # ---- reading -------------------------------------------------------------------------------------
    def find(args):
        k = kit()
        hits = k.search.where(str(args.get("query") or ""), since=day_arg(args, "since"), until=day_arg(args, "until"))
        if not hits:
            return "I can't find that in your notes or our conversations."
        from minty_core.vault.search import render
        return render(hits)

    def ask(args):
        return kit().search.ask(str(args.get("question") or "")).text

    def open_note(args):
        k = kit()
        note = k.vault.get(str(args.get("id") or "").strip())
        if note is None:
            return "No note with that id."
        from minty_core.vault.search import badge
        tag = TAG_UNTRUSTED if note.confidence == "untrusted" else TAG
        warn = k.search.freshness(note)
        head = f"{note.title} (note:{note.id}) {badge(note)}" + (f"\n⚠ {warn}" if warn else "")
        return f"{tag}\n{head}\n{note.body[:MAX_OPEN]}"

    def resume(args):
        return kit().topics.resume(str(args.get("topic") or ""))

    def resurface(args):
        k = kit()
        today = date.today()
        lines = [f"- On this day: {n.title} (note:{n.id}, {n.created[:4]})" for n in k.resurface.on_this_day(today)]
        lines += [f"- Time to review: {n.title} (note:{n.id})" for n in k.resurface.due(today)[:5]]
        lines += [f"- Decision to revisit: {n.title} (note:{n.id})" for n in k.decisions.due(today)[:5]]
        return TAG + "\n" + "\n".join(lines) if lines else "Nothing is due to resurface today."

    def status(args):
        k = kit()
        st = k.vault.status()
        broken = k.vault.broken_links()
        out = [f"{st['notes']} notes, {st['inbox']} in the inbox; versioning: {st['versioning']}.",
               f"{len(broken)} broken link{'s' * (len(broken) != 1)}." if broken else "No broken links.",
               f"{len(k.pipeline.pending())} inbox suggestions waiting."]
        if ctx.services["config"]().vault.hybrid:
            state = k.hybrid_state()
            out.append("Hybrid search is " + ("on." if state == "on" else state + "."))
        return " ".join(out)

    # ---- writing -------------------------------------------------------------------------------------
    def note(args):
        folder = str(args.get("folder") or "inbox")
        n = kit().vault.create(str(args.get("title") or ""), str(args.get("text") or ""),
                               folder=folder if folder in WRITE_FOLDERS else "inbox", origin=origin(),
                               tags=[str(t) for t in (args.get("tags") or [])][:6],
                               project=str(args.get("project") or ""), source="voice or chat")
        where = "your inbox" if n.folder == "inbox" else n.folder
        return f'Saved to {where} as "{n.title}" (note:{n.id}).'

    def append(args):
        n = kit().vault.append(str(args.get("id") or ""), str(args.get("text") or ""), origin=origin())
        return f'Appended to "{n.title}" (note:{n.id}).'

    def inbox_review(args):
        k = kit()
        k.pipeline.propose()
        pend = k.pipeline.pending()
        if not pend:
            return "Your inbox has nothing waiting for a decision."
        lines = []
        for p in pend[:10]:
            extra = ", ".join(x for x in (f"project {p.project}" if p.project else "",
                                          f"tags {', '.join(p.tags)}" if p.tags else "") if x)
            lines.append(f'- "{p.title}" (note:{p.note_id}) → {p.folder}' + (f" ({extra})" if extra else ""))
        return TAG + "\n" + f"{len(pend)} suggestion{'s' * (len(pend) != 1)}:\n" + "\n".join(lines)

    def decide_risk(args):
        return 2 if str(args.get("action") or "approve") == "approve" else 1

    def decide_describe(args):
        n = kit().vault.get(str(args.get("note_id") or ""))
        prop = next((p for p in kit().pipeline.pending() if p.note_id == str(args.get("note_id"))), None)
        return f"Move '{n.title if n else 'that note'}' to {prop.folder if prop else 'its suggested folder'}"

    def inbox_decide(args):
        k = kit()
        nid, action = str(args.get("note_id") or ""), str(args.get("action") or "approve")
        if action == "reject":
            k.pipeline.reject(nid)
            return "Left it in the inbox; I won't suggest it again until it changes."
        try:
            n = k.pipeline.approve(nid)
        except ValueError as e:
            return str(e)
        return f'Moved "{n.title}" to {n.folder}.'

    def decision_log(args):
        n = kit().decisions.log(str(args.get("title") or ""), decision=str(args.get("decision") or ""),
                                why=str(args.get("why") or ""), options=args.get("options") or [],
                                revisit=str(args.get("revisit") or "") or None, origin=origin())
        return f'Logged the decision "{n.title}" (note:{n.id}).'

    def decision_weigh(args):
        v = weigh(list(args.get("options") or []))
        text = v.summary + " You decide: this only adds up what you told me."
        if args.get("save") and v.ranked:
            n = kit().decisions.save_verdict(str(args.get("question") or "Decision"), v, origin=origin())
            text += f" Saved as note:{n.id}."
        return text

    def bookmark(args):
        n = kit().bookmarks.add(str(args.get("url") or ""), title=str(args.get("title") or ""),
                                summary=str(args.get("summary") or ""), tags=[str(t) for t in args.get("tags") or []][:4],
                                origin=origin())
        return f'Bookmarked "{n.title}" (note:{n.id}).'

    def glossary(args):
        kit().glossary.add(str(args.get("term") or ""), str(args.get("meaning") or ""))
        return f"Added {args.get('term')} to your words."

    def daily(args):
        day = date.fromisoformat(day_arg(args, "date")) if day_arg(args, "date") else date.today()
        n = kit().daily.compose(day, plan=args.get("plan") or [], done=args.get("done") or [],
                                journal=args.get("journal") or [])
        return f"Updated the daily note for {day.isoformat()} (note:{n.id})."

    def review(args):
        k = kit()
        nid, action = str(args.get("note_id") or ""), str(args.get("action") or "add")
        if action == "add":
            n = k.resurface.add_to_review(nid)
            return f'"{n.title}" will come back for review tomorrow.'
        k.resurface.mark(nid, remembered=action == "remembered")
        return "Noted; I'll ask again later." if action == "remembered" else "Back to tomorrow for that one."

    def private():
        if not ctx.services["config"]().vault.encryption:
            raise ValueError("The private notes are switched off (vault.encryption on the Switches page).")
        area = kit().private
        if not area.exists():
            raise PrivateError("There is no private area yet. Make one at the terminal: minty notes private-create")
        return area

    def private_add(args):
        ident = private().add(str(args.get("title") or ""), str(args.get("text") or ""),
                              [str(t) for t in args.get("tags") or []])
        return f"Saved privately ({ident})."

    def private_find(args):
        hits = private().search(str(args.get("query") or ""))
        return "\n".join(f"{h['id']}: {h['title']}" for h in hits) if hits else "No private note matches that."

    def private_open(args):
        n = private().get(str(args.get("id") or ""))
        return f"{n['title']} ({n['id']})\n{n['body']}"

    obj = {"type": "object", "properties": {}}

    def schema(props, required=()):
        return {"type": "object", "properties": props, "required": list(required)}

    s = {"type": "string"}
    arr = {"type": "array", "items": {"type": "string"}}
    return [
        Tool("note", "Save a note to the user's notes folder (default inbox).",
             schema({"title": s, "text": s, "folder": {"type": "string", "enum": list(WRITE_FOLDERS)}, "tags": arr,
                     "project": s}, ["title", "text"]), guarded(note), risk=1),
        Tool("append", "Add text to an existing note by id.", schema({"id": s, "text": s}, ["id", "text"]),
             guarded(append), risk=1),
        Tool("open", "Read one note by id.", schema({"id": s}, ["id"]), open_note),
        Tool("find", "Where did I see that: search notes and past conversations, optionally between since/until "
             "(YYYY-MM-DD).", schema({"query": s, "since": s, "until": s}, ["query"]), find, sensitive=True),
        Tool("ask", "Answer from the user's notes with note ids; says when the notes don't cover it.",
             schema({"question": s}, ["question"]), ask),
        Tool("inbox_review", "List suggestions for filing the inbox.", obj, guarded(inbox_review)),
        Tool("inbox_decide", "Approve (move) or reject a filing suggestion.",
             schema({"note_id": s, "action": {"type": "string", "enum": ["approve", "reject"]}}, ["note_id"]),
             guarded(inbox_decide), risk=decide_risk, describe=decide_describe),
        Tool("decision_log", "Record a decision with why, options and an optional revisit date.",
             schema({"title": s, "decision": s, "why": s, "options": arr, "revisit": s}, ["title", "decision"]),
             guarded(decision_log), risk=1),
        Tool("decision_weigh", "Add up weighted pros and cons per option (weights 1-5); optionally save.",
             schema({"question": s, "options": {"type": "array", "items": {"type": "object"}},
                     "save": {"type": "boolean"}}, ["options"]), decision_weigh,
             risk=lambda args: 1 if args.get("save") else 0),
        Tool("bookmark", "Save a web address with a short summary.",
             schema({"url": s, "title": s, "summary": s, "tags": arr}, ["url"]), guarded(bookmark), risk=1),
        Tool("glossary", "Teach a personal word or abbreviation.", schema({"term": s, "meaning": s},
                                                                           ["term", "meaning"]), guarded(glossary),
             risk=1),
        Tool("daily", "Build or refresh today's daily note.", schema({"date": s, "plan": arr, "done": arr,
                                                                      "journal": arr}), guarded(daily), risk=1),
        Tool("resurface", "Notes from this day in past years, reviews due, decisions to revisit.", obj, resurface),
        Tool("review", "Put a note into spaced review, or mark it remembered/forgotten.",
             schema({"note_id": s, "action": {"type": "string", "enum": ["add", "remembered", "forgot"]}},
                    ["note_id"]), guarded(review), risk=1),
        Tool("resume", "Where you left off on a topic: notes, decisions, open items, conversations.",
             schema({"topic": s}, ["topic"]), resume, sensitive=True),
        Tool("status", "Vault size, versioning and broken links.", obj, status),
        Tool("private_add", "Save an encrypted private note (works only while the private notes are unlocked).",
             schema({"title": s, "text": s, "tags": arr}, ["title", "text"]), guarded(private_add), risk=1),
        Tool("private_find", "Search the unlocked private notes; the answer is shown to the user only.",
             schema({"query": s}, ["query"]), guarded(private_find), local_only=True),
        Tool("private_open", "Open one unlocked private note; shown to the user only.", schema({"id": s}, ["id"]),
             guarded(private_open), local_only=True),
    ]
