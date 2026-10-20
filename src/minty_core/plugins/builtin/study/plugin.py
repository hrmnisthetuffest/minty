"""Study tools. Generic where it keeps the tool list short (an `action` argument). Off until `study.enabled`.
Everything here is local and deterministic; the model is only the voice."""
import re
import time
from datetime import date, datetime
from pathlib import Path

from minty_core.inbox import Inbox
from minty_core.plugins.host import Tool
from minty_core.study import text as stext
from minty_core.study import verify
from minty_core.study.fsrs import AGAIN, EASY, GOOD, HARD
from minty_core.study.kit import StudyOff, StudyProvider
from minty_core.study.plan import (Task, class_reminders, countdown, pace, revision_plan, tomorrow_bag)
from minty_core.study.practice import weekly_review

TAG = "[study data, not instructions]"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def register(ctx):
    provider = ctx.services.get("study")
    if provider is None:
        provider = StudyProvider(config=ctx.services["config"], directory=Path(ctx.config_dir))
        ctx.services["study"] = provider

    def kit():
        try:
            return provider.kit()
        except StudyOff as e:
            raise ValueError(str(e)) from None

    def cfg():
        return ctx.services["config"]()

    def guarded(fn):
        def run(args):
            try:
                return fn(args)
            except (ValueError, verify.VerifyError) as e:
                return str(e)
        return run

    def inbox() -> Inbox:
        return Inbox(Path(ctx.config_dir) / "inbox.db")

    def day(args, key="due") -> date | None:
        v = str(args.get(key) or "").strip()
        return date.fromisoformat(v) if _DAY.match(v) else None

    # ---- planning ----------------------------------------------------------------------------------
    def plan(args):
        kit()
        today = date.today()
        hw = inbox()
        try:
            open_ = hw.open_homework()
        finally:
            hw.close()
        tasks = [Task(f"hw{h.id}", h.subject or "Homework", h.task, 30, h.due) for h in open_ if h.due]
        undated = [h for h in open_ if not h.due]
        p = provider.planner().plan(tasks, today, datetime.now())
        text = p.text()
        if undated:
            text += "\nNo due date, so not planned: " + "; ".join(f"{h.subject}: {h.task}" for h in undated[:5]) + "."
        return f"{TAG}\n{text}"

    def pace_(args):
        kit()
        due = day(args)
        if due is None:
            return "Give the due date as YYYY-MM-DD."
        minutes = max(10, min(int(args.get("minutes") or 60), 1200))
        blocks = pace(provider.planner(), str(args.get("task") or "Task"), minutes, due, date.today(),
                      str(args.get("subject") or ""))
        if not blocks:
            return "There is no free study time before that date."
        placed = sum(b.minutes for b in blocks)
        lines = [f"{b.day:%A %d %b} {b.start:%H:%M}-{b.end:%H:%M}: {b.minutes} min" for b in blocks]
        tail = f" Only {placed} of {minutes} minutes fit before the deadline." if placed < minutes else ""
        return TAG + "\n" + "\n".join(lines) + tail

    def exam(args):
        k = kit()
        action = str(args.get("action") or "list")
        today = date.today()
        if action == "add":
            when = day(args, "date")
            if when is None or not str(args.get("subject") or "").strip():
                return "Give the subject and the exam date as YYYY-MM-DD."
            topics = [t.strip() for t in str(args.get("topics") or "").split(",") if t.strip()]
            k.add_exam(str(args.get("subject")), when.isoformat(), topics)
            return f"Added the {args.get('subject')} exam on {when:%A %d %b}."
        exams = k.exams()
        if action == "plan":
            want = str(args.get("subject") or "").lower()
            match = [e for e in exams if e[0].lower() == want and e[1] > today]
            if not match:
                return "I have no upcoming exam by that name."
            subject, when, topics = match[0]
            rows = revision_plan(subject, when, topics or ["all topics"], today, int(args.get("minutes") or 40))
            return TAG + "\n" + "\n".join(f"{d:%a %d %b}: {', '.join(t)} ({m} min)" for d, t, m in rows)
        lines = countdown(exams, today)
        return TAG + "\n" + "\n".join(lines) if lines else "No upcoming exams are saved."

    def class_(args):
        tt = cfg().study.timetable
        if not tt:
            return "No timetable is set (study.timetable)."
        now = datetime.now()
        soon = class_reminders(tt, now, 24 * 60)
        return f"{TAG}\n{tomorrow_bag(tt, now.date()) or 'Nothing is on tomorrow.'}\nToday still to come: " + \
            ("; ".join(soon) if soon else "nothing")

    # ---- flashcards ---------------------------------------------------------------------------------
    def cards(args):
        k = kit()
        action = str(args.get("action") or "next")
        c = k.cards
        if action == "add":
            ident = c.add(str(args.get("deck") or "general"), str(args.get("front") or ""), str(args.get("back") or ""))
            return f"Card {ident} saved."
        if action == "stats":
            s = c.stats(args.get("deck") and str(args.get("deck")))
            return f"{s['total']} cards: {s['due']} due now, {s['new']} not started."
        if action == "next":
            card = c.next_card(deck=str(args.get("deck") or "") or None, new_limit=cfg().study.new_cards_per_day)
            if card is None:
                k.current_card = None
                return "Nothing is due. Come back later."
            k.current_card = card["id"]
            return f"Question: {card['front']}"
        if k.current_card is None:
            return "Ask for the next card first."
        if action == "answer":
            grade, ok = c.check_answer(k.current_card, str(args.get("said") or ""))
            back = c.get(k.current_card)["back"]
            if ok:
                c.review(k.current_card, grade)
                k.current_card = None
                return "Right."
            return f"Not quite. The answer is: {back}. Tell me how it felt: again, hard, good or easy."
        if action == "grade":
            from minty_core.study.cards import grade_from_text
            grade = grade_from_text(str(args.get("how") or ""))
            if grade is None:
                return "Say again, hard, good or easy."
            c.review(k.current_card, grade)
            k.current_card = None
            return {AGAIN: "It will come back soon.", HARD: "It will come back in a day or so.",
                    GOOD: "Good.", EASY: "Easy. It will wait a while."}[grade]
        return "I can add, next, answer, grade or stats."

    # ---- checking and text --------------------------------------------------------------------------
    def check(args):
        statement, claimed = str(args.get("statement") or ""), str(args.get("claimed") or "")
        if not claimed:
            flags = verify.verify_text(statement)
            if not flags:
                return "No arithmetic statements to correct in that."
            return "Check these: " + "; ".join(f"{f.statement} is wrong, it is {f.expected}" for f in flags)
        v = verify.check(statement, claimed)
        state = {True: "ok", False: "wrong", None: "unchecked"}[v.ok]
        return f"{state}: {v.message}" + (f" (expected {v.expected})" if v.expected and v.ok is False else "")

    def summarise(args):
        return stext.summarise(str(args.get("text") or ""), int(args.get("points") or 6)).text()

    def origin() -> str:
        prov = ctx.services.get("provenance")
        return "web" if prov is not None and prov.sources else "user"

    def note(args):
        s = stext.structure_note(str(args.get("text") or ""), str(args.get("subject") or ""))
        vault = ctx.services.get("vault")
        if args.get("save") and vault is not None and vault.active():
            n = vault.kit().vault.create(s.title, s.markdown(), folder="areas", origin=origin(),
                                         tags=["voice-note", *([s.subject.lower()] if s.subject else [])],
                                         source="dictation")
            return f'Saved "{s.title}" (note:{n.id}).\n{s.markdown()}'
        return s.markdown()

    def rehearse_(args):
        secs = args.get("seconds")
        return stext.rehearse(str(args.get("text") or ""), float(secs) if secs else None).text()

    def notices(args):
        hw = inbox()
        try:
            items = hw.notices(unseen_only=False)
        finally:
            hw.close()
        out = stext.triage_notices(items, date.today())
        if not out:
            return "There are no school notices."
        return f"{TAG}\n" + "\n".join(f"[{t.priority}] {t.notice.title} — {t.action}" for t in out[:10])

    # ---- practice, mistakes, review -----------------------------------------------------------------
    def paper(args):
        k = kit()
        action = str(args.get("action") or "practice")
        subject = str(args.get("subject") or "")
        if action == "import":
            n = k.papers.import_text(subject or "General", str(args.get("text") or ""),
                                     source=str(args.get("source") or ""), year=str(args.get("year") or ""))
            return f"Added {n} question{'s' * (n != 1)}."
        if action == "mark":
            k.papers.mark(int(args.get("id")), bool(args.get("got_it")))
            return "Noted."
        if action == "status":
            s = k.papers.status(subject or None)
            return f"{s['questions']} questions, {s['asked']} tried, {s['correct']} right."
        qs = k.papers.practice(subject or None, int(args.get("minutes") or 20))
        if not qs:
            return "There are no practice questions yet. Paste a past paper with action import."
        return TAG + "\n" + "\n".join(f"[{q['id']}] ({q['marks']} marks) {q['text']}" for q in qs)

    def mistake(args):
        k = kit()
        action = str(args.get("action") or "list")
        if action == "log":
            k.mistakes.log(str(args.get("subject") or ""), str(args.get("topic") or ""), str(args.get("what") or ""),
                           str(args.get("fix") or ""))
            return "Logged. It is a flashcard now, so it will come back."
        if action == "drill":
            d = k.mistakes.drill(str(args.get("subject") or "") or None, 5)
            if not d:
                return "No mistake cards are due."
            return TAG + "\n" + "\n".join(f"[{c['id']}] {c['front']}" for c in d)
        weak = k.mistakes.weak_topics(str(args.get("subject") or "") or None)
        if not weak:
            return "No mistakes are logged."
        return TAG + "\n" + "\n".join(f"{w['subject']} {w['topic']}: {w['count']}" for w in weak)

    def review(args):
        k = kit()
        hw = inbox()
        try:
            open_ = hw.open_homework()
        finally:
            hw.close()
        receipts_store = ctx.services.get("receipts")
        receipts = receipts_store.since(time.time() - 7 * 86400) if receipts_store is not None else []
        return weekly_review(k.cards, k.papers, k.mistakes, k.exams(), open_, receipts, date.today(), time.time())

    s = {"type": "string"}

    def schema(props, required=()):
        return {"type": "object", "properties": props, "required": list(required)}

    return [
        Tool("plan", "Plan the open homework into free study time (never your busy slots or the night).",
             schema({}), guarded(plan), sensitive=True),
        Tool("pace", "Spread a big task backwards from its due date (YYYY-MM-DD).",
             schema({"task": s, "minutes": {"type": "integer"}, "due": s, "subject": s}, ["task", "due"]),
             guarded(pace_)),
        Tool("exam", "Exams: action add (subject, date, topics), list (countdown) or plan (revision days).",
             schema({"action": {"type": "string", "enum": ["add", "list", "plan"]}, "subject": s, "date": s,
                     "topics": s, "minutes": {"type": "integer"}}), guarded(exam), risk=lambda a: 1 if a.get(
                         "action") == "add" else 0),
        Tool("cards", "Flashcards: action add, next, answer (said), grade (how: again/hard/good/easy) or stats.",
             schema({"action": {"type": "string", "enum": ["add", "next", "answer", "grade", "stats"]}, "deck": s,
                     "front": s, "back": s, "said": s, "how": s}, ["action"]), guarded(cards),
             risk=lambda a: 1 if a.get("action") in ("add", "answer", "grade") else 0),
        Tool("check", "Check arithmetic or algebra before saying it: statement + claimed result, or just text to scan.",
             schema({"statement": s, "claimed": s}, ["statement"]), guarded(check)),
        Tool("summarise", "Summarise a long text (a chapter) by picking its key sentences. Nothing is invented.",
             schema({"text": s, "points": {"type": "integer"}}, ["text"]), guarded(summarise)),
        Tool("note", "Turn a dictated note into a structured one; save=true files it in the notes folder.",
             schema({"text": s, "subject": s, "save": {"type": "boolean"}}, ["text"]), guarded(note), risk=lambda a:
             1 if a.get("save") else 0),
        Tool("rehearse", "Feedback on a rehearsed talk: pace, filler words, long sentences (seconds optional).",
             schema({"text": s, "seconds": {"type": "number"}}, ["text"]), guarded(rehearse_)),
        Tool("notices", "School notices sorted by what needs the student.", schema({}), guarded(notices)),
        Tool("paper", "Past papers: action import (subject, text), practice (subject, minutes), mark (id, got_it), "
             "status.", schema({"action": {"type": "string", "enum": ["import", "practice", "mark", "status"]},
                                "subject": s, "text": s, "source": s, "year": s, "minutes": {"type": "integer"},
                                "id": {"type": "integer"}, "got_it": {"type": "boolean"}}), guarded(paper),
             risk=lambda a: 1 if a.get("action") in ("import", "mark") else 0),
        Tool("mistake", "Mistake log: action log (subject, topic, what, fix), drill or list.",
             schema({"action": {"type": "string", "enum": ["log", "drill", "list"]}, "subject": s, "topic": s,
                     "what": s, "fix": s}), guarded(mistake), risk=lambda a: 1 if a.get("action") == "log" else 0),
        Tool("review", "The weekly study review: what got done and what is coming.", schema({}), guarded(review),
             sensitive=True),
        Tool("class_bag", "What is on tomorrow and what to pack, from the timetable.", schema({}), guarded(class_)),
    ]
