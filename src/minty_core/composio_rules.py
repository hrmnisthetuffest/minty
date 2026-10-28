"""What a Composio step is, from its name and the thing it names: a read, a draft, a write into a sheet or doc, or an act on people.

Composio runs thousands of actions behind one tool (`COMPOSIO_MULTI_EXECUTE_TOOL`), so Minty's question cannot be about the tool, it
has to be about the steps in the call. Plain word checks on the slug (`GMAIL_FETCH_EMAILS`, `GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND`)
and on the target (a sheet or doc id), never a model's judgment, so a page or an e-mail cannot talk its way past them. Anything
this does not recognise is an act, and an act is asked.

Classes it gives a whole call: 1 only reads or drafts (nothing leaves, nothing of anyone else's changes), 2 also writes into a sheet or
doc Minty made or you named (or makes a new one), 3 anything else (sending, deleting, inviting, an unknown step, a target nobody
registered). Class 3 is always asked; class 2 is asked in careful and commits modes unless your own request already says it.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

READ = frozenset({"FETCH", "GET", "LIST", "SEARCH", "FIND", "READ", "RETRIEVE", "LOOKUP", "CHECK", "COUNT", "QUERY", "VIEW", "DESCRIBE"})
# a word that changes something or reaches someone; a name with one of these is never a read, whatever else it says
WRITE = frozenset({
    "SEND", "CREATE", "DELETE", "UPDATE", "POST", "REPLY", "FORWARD", "REMOVE", "MOVE", "ARCHIVE", "TRASH", "ADD", "INSERT", "PATCH", "EDIT",
    "WRITE", "UPLOAD", "MODIFY", "SET", "CLEAR", "EXECUTE", "RUN", "SHARE", "INVITE", "CANCEL", "BATCH", "MERGE", "PUBLISH", "DEPLOY",
    "APPROVE", "REJECT", "ASSIGN", "TRANSFER", "PAY", "CHARGE", "REFUND", "SUBSCRIBE", "UNSUBSCRIBE", "ENABLE", "DISABLE", "REVOKE", "GRANT",
    "START", "STOP", "TRIGGER", "COMMENT", "REACT", "FOLLOW", "BLOCK", "MARK", "SAVE", "IMPORT", "APPEND", "FORMAT", "COPY", "RENAME",
    "RESTORE", "CLOSE", "REOPEN", "LOCK", "UNLOCK", "RESET", "REGISTER", "SIGN", "SUBMIT", "ORDER", "BUY", "PUT", "LABEL"})
ARTIFACT_TOOLKITS = frozenset({"GOOGLESHEETS", "GOOGLEDOCS"})
ARTIFACT_VERBS = frozenset({"APPEND", "UPDATE", "INSERT", "ADD", "BATCH", "CREATE", "FORMAT", "WRITE"})
# in a sheet or doc these still reach beyond it (or destroy what is in it)
NOT_QUIET = frozenset({"CLEAR", "DELETE", "REMOVE", "SHARE", "PERMISSION", "PERMISSIONS", "TRASH", "MOVE", "TRANSFER", "OWNER", "PUBLISH",
                       "EMAIL", "SEND", "NOTIFY", "COPY"})
DRAFT_OK = frozenset({"CREATE", "UPDATE"})
ID_KEYS = ("spreadsheet_id", "spreadsheetId", "document_id", "documentId")
_LINK = re.compile(r"https://docs\.google\.com/(spreadsheets|document)/d/([A-Za-z0-9_-]{20,})(?:/[A-Za-z0-9_?=&#%.-]*)?")
_KIND = {"spreadsheets": "sheet", "document": "doc"}
_MAKES = {"GOOGLESHEETS": "spreadsheets", "GOOGLEDOCS": "document"}


def _tokens(slug: str) -> tuple[str, set[str], list[str]]:
    parts = re.sub(r"[^A-Z0-9_]", "", str(slug or "").upper()).split("_")
    return parts[0], set(parts[1:]), parts[1:]


def step_kind(slug: str) -> str:
    """read | draft | artifact | act, for one step's slug."""
    toolkit, tokens, ordered = _tokens(slug)
    if not toolkit or not ordered:
        return "act"
    writes = tokens & WRITE
    if not writes and ordered and (set(ordered[:3]) | {ordered[-1]}) & READ:
        return "read"
    if toolkit == "GMAIL" and "DRAFT" in tokens and writes <= DRAFT_OK and writes:
        return "draft"  # prepared in the mailbox, sent by nobody
    if toolkit in ARTIFACT_TOOLKITS and writes and (tokens & ARTIFACT_VERBS) and not (tokens & NOT_QUIET):
        return "artifact"
    return "act"


def artifact_ids(arguments) -> list[str]:
    """The sheet or doc ids a step names, whatever the argument is called."""
    return [str(arguments[k]) for k in ID_KEYS if isinstance(arguments, dict) and arguments.get(k)]


def _steps(arguments) -> list[tuple[str, dict]] | None:
    steps = arguments.get("tools") if isinstance(arguments, dict) else None
    if not isinstance(steps, list) or not steps:
        return None
    out = []
    for step in steps:
        if not isinstance(step, dict):
            return None
        args = step.get("arguments")
        out.append((str(step.get("tool_slug") or ""), args if isinstance(args, dict) else {}))
    return out


def call_class(arguments, known: Callable[[str], bool]) -> int:
    """The risk class of a whole COMPOSIO_MULTI_EXECUTE_TOOL call (see the module note). `known(id)`: a sheet or doc Minty may write to."""
    steps = _steps(arguments)
    if steps is None:
        return 3
    level = 1
    for slug, args in steps:
        kind = step_kind(slug)
        if kind == "act":
            return 3
        if kind == "artifact":
            if "CREATE" not in _tokens(slug)[1]:  # a new one has no target yet; changing an existing one needs a target Minty knows
                ids = artifact_ids(args)
                if not ids or not all(known(i) for i in ids):
                    return 3
            level = 2
    return level


def created_artifacts(arguments, answer: str) -> list[tuple[str, str, str, str]]:
    """(kind, id, link, title) of what the create steps of a call made, read from the links in Composio's answer; each once."""
    steps = _steps(arguments) or []
    wanted = []
    for slug, args in steps:
        toolkit, tokens, _ = _tokens(slug)
        if toolkit in _MAKES and "CREATE" in tokens:
            wanted.append((_MAKES[toolkit], str(args.get("title") or args.get("name") or "")[:120]))
    found, seen = [], set()
    for match in _LINK.finditer(answer or ""):
        kind_word, ext_id = match.group(1), match.group(2)
        title = next((t for k, t in wanted if k == kind_word), None)
        if title is None or ext_id in seen:
            continue
        seen.add(ext_id)
        found.append((_KIND[kind_word], ext_id, match.group(0), title))
    return found


SEND_KEYS = frozenset({"recipient_email", "subject", "body", "is_html", "include_signature"})
_SEND_WORDS = re.compile(r"\b(send|email|e-mail|mail|write to|message)\b", re.IGNORECASE)
_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,5}")
_LINK_WORDS = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|dev|app|xyz|top|ru|cn|info|biz|co|in|me|ly|sh)\b", re.IGNORECASE)


def checked_send(arguments, request: str, sensitive: Iterable[str]) -> bool:
    """True only for one plain GMAIL_SEND_EMAIL the user asked for: a single recipient whose address is in the user's own words, a short
    plain note (no link, no other address, no sensitive word, nothing that reads as an order to a model), no cc, bcc, attachment or html."""
    from minty_core.provenance import looks_like_injection

    steps = _steps(arguments)
    if steps is None or len(steps) != 1 or steps[0][0].upper() != "GMAIL_SEND_EMAIL":
        return False
    args = steps[0][1]
    if set(args) - SEND_KEYS or args.get("is_html"):
        return False
    to, subject, body = args.get("recipient_email"), args.get("subject", ""), args.get("body", "")
    if not (isinstance(to, str) and isinstance(subject, str) and isinstance(body, str)) or not _ADDRESS.fullmatch(to):
        return False
    if not re.search(rf"(?<![A-Za-z0-9._%+-]){re.escape(to)}(?![A-Za-z0-9._%+-])", request or "", re.IGNORECASE) or not _SEND_WORDS.search(request or ""):
        return False  # the address as its own word: "pam@x.test" does not make "am@x.test" the user's
    if not body.strip() or len(body) > 1200 or len(subject) > 150 or "\n" in subject:
        return False
    text = f"{subject}\n{body}"
    if _LINK_WORDS.search(text) or _ADDRESS.search(text) or looks_like_injection(text):
        return False
    return not any(re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE) for w in sensitive)
