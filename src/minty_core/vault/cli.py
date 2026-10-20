"""`minty-core notes ...`: the vault in the terminal. It works on the files directly, so it does not need the
assistant to be running. Nothing here can delete a note."""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
from typing import Callable, TextIO

from minty_core.vault.bookmarks import BookmarkError
from minty_core.vault.kit import VaultOff
from minty_core.vault.search import badge
from minty_core.vault.store import VaultError

USAGE = """minty-core notes <command>
  list [folder]                       the newest notes (optionally one folder)
  search <words>                      find in notes and past conversations
  show <id>                           print a note
  new <title> [--folder F] [--tag T] [--project P]    text is read from standard input
  edit <id>                           open the note in $EDITOR and save the change
  inbox                               filing suggestions waiting for you
  approve <id> | reject <id>          decide a suggestion
  check                               size, versioning and broken links
  gate <questions.json> [--min N]     measure meaning-based search on your own questions (the 604 gate)
  private-create                      make the encrypted private area (asks for a passphrase twice)
  unlock | lock                       open or close the private notes in the running Minty (asks for the passphrase)
"""


def external_editor(text: str) -> str:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        subprocess.run([editor, path], check=False)
        with open(path, encoding="utf-8") as f:
            return f.read()
    finally:
        os.unlink(path)


def _flag(argv: list[str], name: str, many: bool = False):
    values: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == name and i + 1 < len(argv):
            values.append(argv[i + 1])
            del argv[i:i + 2]
        else:
            i += 1
    return values if many else (values[-1] if values else "")


def run(argv: list[str], provider, *, out: TextIO = sys.stdout, stdin: TextIO = sys.stdin,
        editor: Callable[[str], str] = external_editor, ask_secret: Callable[[str], str] = getpass.getpass) -> int:
    argv = list(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        out.write(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    try:
        kit = provider.kit()
    except VaultOff as e:
        out.write(f"{e}\n")
        return 1
    try:
        if cmd == "private-create":
            return _private_create(kit, provider, out, ask_secret)
        return _dispatch(cmd, rest, kit, out, stdin, editor)
    except (VaultError, BookmarkError, ValueError) as e:
        out.write(f"{e}\n")
        return 1


def _private_create(kit, provider, out, ask_secret) -> int:
    if not provider.config().vault.encryption:
        out.write("Turn on vault.encryption on the Switches page first.\n")
        return 1
    if kit.private.exists():
        out.write("The private area already exists.\n")
        return 1
    first = ask_secret("Passphrase for the private notes (8+ characters): ")
    if ask_secret("Again: ") != first:
        out.write("They did not match; nothing was made.\n")
        return 1
    phrase = kit.private.create(first)
    out.write("The private area is ready.\n"
              f"Your recovery phrase (shown once; write it down and keep it away from this computer):\n  {phrase}\n"
              "If you lose both the passphrase and this recovery phrase you lose the private notes: there is no back "
              "door.\nUnlock them with: minty-core notes unlock\n")
    return 0


def _dispatch(cmd, rest, kit, out, stdin, editor) -> int:
    vault = kit.vault
    if cmd == "list":
        for n in vault.list(rest[0] if rest else None, limit=30):
            out.write(f"{n.id}  {n.path}  {n.title}\n")
        return 0
    if cmd == "search":
        hits = kit.search.where(" ".join(rest))
        for h in hits:
            out.write(f"{h.title or 'conversation'}  {h.badge}\n    {h.snippet}\n"
                      + (f"    ⚠ {h.warning}\n" if h.warning else ""))
        if not hits:
            out.write("Nothing matches that in your notes or past conversations.\n")
        return 0
    if cmd == "show":
        n = vault.get(rest[0]) if rest else None
        if n is None:
            out.write("No note with that id.\n")
            return 1
        warn = kit.search.freshness(n)
        out.write(f"{n.title}  (note:{n.id})  {badge(n)}\n{n.path}\n" + (f"⚠ {warn}\n" if warn else "")
                  + f"\n{n.body}")
        return 0
    if cmd == "new":
        rest = list(rest)
        folder, project = _flag(rest, "--folder") or "inbox", _flag(rest, "--project")
        tags = _flag(rest, "--tag", many=True)
        n = vault.create(" ".join(rest), stdin.read(), folder=folder, origin="user", tags=tags, project=project,
                         source="terminal")
        out.write(f'Saved "{n.title}" to {n.folder} (note:{n.id}).\n')
        return 0
    if cmd == "edit":
        n = vault.get(rest[0]) if rest else None
        if n is None:
            out.write("No note with that id.\n")
            return 1
        new = editor(n.body)
        if new == n.body:
            out.write("No changes.\n")
            return 0
        vault.update(n.id, body=new, origin="user")
        out.write(f'Saved "{n.title}".\n')
        return 0
    if cmd == "inbox":
        kit.pipeline.propose()
        pend = kit.pipeline.pending()
        for p in pend:
            extra = ", ".join(x for x in (f"project {p.project}" if p.project else "",
                                          f"tags {', '.join(p.tags)}" if p.tags else "") if x)
            out.write(f'{p.note_id}  "{p.title}" -> {p.folder}' + (f" ({extra})" if extra else "") + "\n")
        if not pend:
            out.write("Nothing is waiting in the inbox.\n")
        return 0
    if cmd == "approve" and rest:
        n = kit.pipeline.approve(rest[0])
        out.write(f'Moved "{n.title}" to {n.folder}.\n')
        return 0
    if cmd == "reject" and rest:
        kit.pipeline.reject(rest[0])
        out.write("Left it in the inbox.\n")
        return 0
    if cmd == "gate" and rest:
        from pathlib import Path

        from minty_core.vault.gate import MIN_QUESTIONS, evaluate, load_questions, save_result

        rest = list(rest)
        minimum = int(_flag(rest, "--min") or MIN_QUESTIONS)
        result = evaluate(vault, load_questions(Path(rest[0]), vault), min_questions=minimum)
        save_result(kit.gate_path, result)
        out.write(("Passed: " if result.passed else "Not passed: ") + result.reason + ".\n"
                  f"Plain search found the right note in the top five {result.baseline_recall:.0%} of the time, "
                  f"the candidate {result.candidate_recall:.0%} (a gain of {result.gain_points:.1f} points), "
                  f"{result.candidate_ms:.1f} ms a query on {result.machine or 'this machine'}.\n")
        if result.passed:
            out.write("Switch on vault.hybrid to use it. Re-run the gate on the laptop first if that is where it "
                      "will run.\n")
        return 0
    if cmd == "check":
        st, broken = vault.status(), vault.broken_links()
        out.write(f"{st['notes']} notes, {st['inbox']} in the inbox; versioning: {st['versioning']}.\n")
        out.write((f"{len(broken)} broken link{'s' * (len(broken) != 1)}:\n"
                   + "".join(f"  {s} -> {d}\n" for s, d in broken)) if broken else "No broken links.\n")
        return 0
    out.write(USAGE)
    return 1
