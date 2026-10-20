"""The daily git digest (647): what was committed since a time, what is uncommitted, and where the branch stands."""
from __future__ import annotations

import re
from pathlib import Path

_SINCE = re.compile(r"[0-9A-Za-z][0-9A-Za-z :.,+-]{0,39}")


async def digest(path: Path, git, since: str = "24 hours ago") -> str:
    since = str(since).strip()
    if not _SINCE.fullmatch(since):
        raise ValueError("Give the time as a date like 2026-10-17 or words like '2 days ago'.")
    log = await git.run(path, "log", f"--since={since}", "--no-merges", "--format=@@%h|%an|%s", "--numstat")
    if not log.ok:
        return f"{Path(path).name} is not a git repository."
    commits, authors, files, added, deleted = [], set(), set(), 0, 0
    for line in log.out.splitlines():
        if line.startswith("@@"):
            h, author, subject = (line[2:].split("|", 2) + ["", ""])[:3]
            commits.append((h, subject))
            authors.add(author)
        elif "\t" in line:
            a, d, f = line.split("\t", 2)
            files.add(f)
            added += int(a) if a.isdigit() else 0
            deleted += int(d) if d.isdigit() else 0
    parts = []
    if commits:
        listing = "; ".join(f"{s[:60]} ({h})" for h, s in commits[:5]) + (f"; and {len(commits) - 5} more" if len(commits) > 5 else "")
        parts.append(f"{len(commits)} commit{'s' * (len(commits) != 1)} since {since} by {', '.join(sorted(authors))}, "
                     f"touching {len(files)} file{'s' * (len(files) != 1)} (+{added} −{deleted}): {listing}.")
    else:
        parts.append(f"No commits since {since}.")
    status = await git.run(path, "status", "--porcelain=v1", "-b")
    rows = status.out.splitlines()
    head = rows[0] if rows and rows[0].startswith("##") else ""
    changed = sum(1 for r in rows[1:] if r[:2].strip() and not r.startswith("??"))
    new = sum(1 for r in rows[1:] if r.startswith("??"))
    parts.append(f"Uncommitted: {changed} changed, {new} new." if changed or new else "Nothing uncommitted.")
    branch = head[3:].split("...")[0] if head else "?"
    m = re.search(r"\[(.*?)\]", head)
    if "..." not in head:
        parts.append(f"Branch {branch}: no upstream.")
    else:
        parts.append(f"Branch {branch}: " + (m.group(1) if m else "level with its upstream") + ".")
    return " ".join(parts)
