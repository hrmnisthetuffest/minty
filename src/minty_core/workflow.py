"""The workflow brain: how big is this goal, and what would make the result useful beyond the literal sentence?

Plain word rules first (no model call, so it is free and repeatable). A simple question gets a direct answer. A goal that
researches, compares, ranks or tracks several things is a *project*, and a project is worth a working record (a sheet), a report (a
doc), reminders for its dates, a list of follow-ups, and a check that what was written really is there. What comes out is a few lines of
background for the mission worker: what to make, where to look first so it does not make a second one, and what not to pretend. It
never grants anything; every write still goes through the safety layer. If no spreadsheet tool is connected it says so, so the worker does
not claim a sheet it could not make. What the user prefers (`prefers`) can add a deliverable to a task but never turns a question into
a project.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

PROJECT_WORDS = re.compile(r"\b(research|investigate|compare|comparison|competitors?|rank|ranking|shortlist|leads?|prospects?|candidates?|"
                           r"organi[sz]e|analy[sz]e|outreach|market|opportunit(?:y|ies)|track|audit)\b", re.I)
MANY = re.compile(r"\b(\d{1,3})\s+(?:\w+\s+){0,3}?(companies|competitors|leads|options|candidates|prospects|places|products|vendors|people|"
                  r"schools|dentists|startups|apps|tools|jobs|listings|articles|papers)\b", re.I)
TABLE = re.compile(r"\b(sheets?|spreadsheets?|table|tracker|compare|comparison|competitors?|rank|ranking|shortlist|leads?|lead list|prospects?|"
                   r"candidates?)\b", re.I)
DOC = re.compile(r"\b(report|write[- ]?up|memo|proposal|document|doc|brief(?:ing)?|summary doc)\b", re.I)
DATES = re.compile(r"\b(deadlines?|due dates?|expir(?:y|es|ation)|renewals?|appointments?)\b", re.I)
STRONG_TABLE = re.compile(r"\b(sheets?|spreadsheets?|tracker|lead list|leads|shortlist|prospect list)\b", re.I)  # a table was as good as asked for
FOLLOW = re.compile(r"\b(outreach|follow[- ]?ups?|email them|contact them|reach out)\b", re.I)


@dataclass(frozen=True)
class Brief:
    kind: str  # answer | task | project
    deliverables: tuple[str, ...] = ()
    reason: str = ""
    no_sheet: bool = False  # a sheet would have helped, but no spreadsheet tool is connected

    def text(self) -> str:
        if not self.deliverables and not self.no_sheet:
            return ""
        lines = ["How to make this useful (the user did not list these; do them as part of the goal and do not ask first):"]
        if "sheet" in self.deliverables:
            lines.append("- Keep the findings in a Google Sheet: call artifacts_list first and reuse a sheet for this subject if there is one, "
                         "otherwise make one. One row per item; columns for the source link, the date you checked, a status, a rank with a "
                         "short reason, and the next action. Update rows that exist instead of adding duplicates, mark what you could not "
                         "confirm, and read back what you wrote to check it is there.")
        if self.no_sheet:
            lines.append("- No spreadsheet tool is connected, so put the table in your final answer and say plainly that no sheet was made.")
        if "doc" in self.deliverables:
            lines.append("- Write the report as a Google Doc (artifacts_list first), with the sources and the date, and say where it is.")
        if "reminders" in self.deliverables:
            lines.append("- Make a reminder for each deadline you find (the reminders tool), and list them in your answer.")
        if "follow-ups" in self.deliverables:
            lines.append("- Prepare the follow-ups (the drafts or messages the goal asks for) as drafts for the user; list who and when. "
                         "Do not send anything the goal did not ask you to send.")
        lines.append("Do not create anything the goal does not need.")
        return "\n".join(lines)


def brief(goal: str, *, can_sheet: bool = False, prefers: Callable[[str], bool] | None = None) -> Brief:
    text = " ".join(str(goal or "").split())
    signals = len(set(m.group(0).lower() for m in PROJECT_WORDS.finditer(text)))
    many = MANY.search(text)
    big_number = bool(many and int(many.group(1)) >= 5)
    if signals + (2 if big_number else 0) >= 3 or (signals >= 2 and big_number):
        kind = "project"
    elif signals >= 1 or big_number:
        kind = "task"
    else:
        return Brief("answer", reason="a plain question or a single action")
    deliverables: list[str] = []
    wants_table = bool(STRONG_TABLE.search(text)) or (kind == "project" and bool(TABLE.search(text)))
    if kind == "task" and prefers is not None and signals and _safe(prefers, "research->sheet"):
        wants_table = True  # the user's own habit: research ends in a sheet
    no_sheet = False
    if wants_table:
        if can_sheet:
            deliverables.append("sheet")
        else:
            no_sheet = True
    if DOC.search(text):  # a report that was asked for is made, however small the rest of the goal is
        deliverables.append("doc")
    if DATES.search(text):  # "note every deadline" is a request for reminders, whatever the size of the goal
        deliverables.append("reminders")
    if kind == "project" and FOLLOW.search(text):
        deliverables.append("follow-ups")
    return Brief(kind, tuple(deliverables), f"{signals} project signals" + (f", about {many.group(1)} items" if many else ""), no_sheet)


def _safe(prefers: Callable[[str], bool], what: str) -> bool:
    try:
        return bool(prefers(what))
    except Exception:  # noqa: BLE001 — a preference that cannot be read is no preference
        return False


def asks_sheet_for_research(goal: str) -> bool:
    """The user asked for research and said where the findings go (a sheet or a tracker): one more sign of the habit."""
    text = " ".join(str(goal or "").split())
    return bool(re.search(r"\b(research|investigate|compare|competitors?)\b", text, re.I)) and bool(STRONG_TABLE.search(text))
