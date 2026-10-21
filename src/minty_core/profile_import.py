"""Bring old free-form profile notes into the 500 questions.

The notes are read as "Label: answer" lines (a bullet, and a "Q12" number, are ignored; an indented line carries on the one above).
Each entry is matched to the question it is about by the words of its label. Only a clear match is used: a vague entry is listed
as unmatched so you can answer it in the panel, never guessed. Nothing is overwritten: a question that is already answered keeps its answer.
`minty profile-import FILE` shows the proposal; with `--apply` it writes the confident matches."""
from __future__ import annotations

import re
from dataclasses import dataclass

from minty_core.profile import MAX_ANSWER, QUESTIONS, Question, ProfileStore, _words

_ENTRY = re.compile(r"^\s*(?:[-*]\s*)?(?:Q\d+\s+)?([A-Za-z][A-Za-z0-9 /&'()-]{1,48}?):\s+(\S.*)$")
_HEADING = re.compile(r"^\s*#")
_SYNONYMS = {  # a label word that stands for words the question uses
    "wake": {"wake", "wake up", "up"}, "location": {"live", "city", "country"}, "birthday": {"birthday"},
    "allergies": {"allergies", "allergic"}, "colour": {"colour"}, "color": {"colour"}, "hobbies": {"hobbies"},
    "languages": {"language", "languages"}, "bed": {"bed"}, "bedtime": {"bed"}, "school": {"school"},
}
_GENERIC = frozenset({"preference", "preferences", "note", "notes", "other", "misc", "general", "info", "details", "likes", "rules"})


@dataclass(frozen=True)
class Entry:
    label: str
    text: str


def parse_notes(text: str) -> list[Entry]:
    entries: list[Entry] = []
    for line in text.splitlines():
        if not line.strip() or _HEADING.match(line):
            continue
        found = _ENTRY.match(line)
        if found and not line.startswith((" ", "\t")):
            entries.append(Entry(found.group(1).strip(), found.group(2).strip()))
        elif entries and line.startswith((" ", "\t")):
            last = entries[-1]
            entries[-1] = Entry(last.label, last.text + " " + line.strip())
    return entries


def _label_words(label: str) -> set[str]:
    words = _words(label)
    for word in list(words):
        words |= _SYNONYMS.get(word, set())
    return words


def match_entry(entry: Entry) -> Question | None:
    """The one question this entry is about, or None when it is vague or ambiguous."""
    if entry.label.lower() in _GENERIC:
        return None
    wanted = _label_words(entry.label)
    if not wanted:
        return None
    answer_words = _words(entry.text)
    scored = []
    for q in QUESTIONS:
        hits = len(wanted & _words(q.text))
        if hits:
            scored.append((hits, len(answer_words & _words(q.text)), q))  # the label decides first, the answer's words break a tie
    if not scored:
        return None
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    best = scored[0]
    if len(wanted) >= 2 and best[0] < 2:
        return None  # a two-word label ("Wake time") must share both words (wake, time)
    if len(scored) > 1 and scored[1][0] == best[0] and scored[1][1] == best[1]:
        return None  # two questions fit equally well
    return best[2]


def run_import(text: str, store: ProfileStore, apply: bool = False) -> dict:
    matched: list[tuple[Entry, Question]] = []
    unmatched: list[Entry] = []
    seen: set[int] = set()
    for entry in parse_notes(text):
        q = match_entry(entry)
        if q is None:
            unmatched.append(entry)
        elif q.id not in seen:
            seen.add(q.id)
            matched.append((entry, q))
    written = skipped = 0
    if apply:
        for entry, q in matched:
            if store.get(q.id):
                skipped += 1
                continue
            store.answer(q.id, entry.text[:MAX_ANSWER])
            written += 1
    return {"matched": matched, "unmatched": unmatched, "written": written, "skipped_existing": skipped}


def format_report(report: dict, applied: bool) -> str:
    lines = [f"{len(report['matched'])} matched, {len(report['unmatched'])} left for you to answer in the panel."]
    for entry, q in report["matched"]:
        lines.append(f"  #{q.id:<3} {q.text}\n        <- {entry.label}: {entry.text[:70]}")
    if report["unmatched"]:
        lines.append("Not matched (answer these in the Profile page):")
        lines += [f"  - {e.label}" for e in report["unmatched"]]
    lines.append(f"Written: {report['written']} (already answered, kept: {report['skipped_existing']})." if applied
                 else "Nothing written yet. Run again with --apply to write the matches.")
    return "\n".join(lines)
