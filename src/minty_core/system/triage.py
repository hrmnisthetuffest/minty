"""Crash triage (633) and the service doctor (634). Both read and summarise; neither restarts, stops, repairs or runs
anything. Log lines are scrubbed of addresses and usernames before they are shown, and kept short."""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass

SIGNALS = {
    "SIGSEGV": "a segmentation fault: it touched memory it should not have",
    "SIGABRT": "it aborted itself, usually after an internal error",
    "SIGILL": "it ran an instruction the processor could not do",
    "SIGBUS": "a memory access error",
    "SIGFPE": "an arithmetic error",
    "SIGKILL": "it was killed from outside, often because the computer ran out of memory",
    "SIGTERM": "it was asked to stop",
}
CAUSES = [
    (r"address already in use", "another program is already using the port it wants"),
    (r"permission denied", "it does not have permission to a file or port it needs"),
    (r"no such file or directory|not found|status=203|failed to execute", "a file it needs is missing or cannot be run"),
    (r"out of memory|oom", "it ran out of memory"),
    (r"timed out|timeout", "it took too long to start"),
    (r"dependency|failed to start .* requires|dependency failed", "something it depends on failed first"),
    (r"segmentation fault|core dumped", "the program itself crashed"),
]
_UNIT = re.compile(r"[A-Za-z0-9:_.@\\-]{1,64}")
_SUFFIXES = (".service", ".timer", ".socket", ".mount", ".target", ".path")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_MAC = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HOME = re.compile(r"/home/[^/\s]+")


def scrub(text: str, limit: int = 120) -> str:
    text = _HOME.sub("/home/~", _EMAIL.sub("<email>", _MAC.sub("<mac>", _IP.sub("<ip>", _HEX.sub("<id>", str(text))))))
    return " ".join(text.split())[:limit]


@dataclass
class CoreRow:
    when: str
    pid: int
    signal: str
    has_core: bool
    exe: str


def parse_coredumps(out: str) -> list[CoreRow]:
    rows = []
    rx = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2}\s+\S+\s+(\d+)\s+\d+\s+\d+\s+(SIG\w+)\s+(\w+)\s+(\S+)")
    for line in str(out).splitlines():
        m = rx.search(line)
        if m:
            rows.append(CoreRow(f"{m.group(1)} {m.group(2)}", int(m.group(3)), m.group(4), m.group(5) in ("present", "journal"),
                                m.group(6)))
    return rows


@dataclass
class JournalRow:
    program: str
    count: int
    sample: str


def summarise_journal(text: str) -> list[JournalRow]:
    counts: Counter[str] = Counter()
    sample: dict[str, str] = {}
    for line in str(text).splitlines():
        m = re.match(r"^\S+\s+\S+\s+([^\s\[:]+)(?:\[\d+\])?:\s*(.*)$", line)
        if not m:
            continue
        prog = m.group(1)
        counts[prog] += 1
        sample.setdefault(prog, scrub(m.group(2)))
    return [JournalRow(p, n, sample[p]) for p, n in counts.most_common()]


def _when(stamp: str) -> str:
    y, mo, d = stamp[:10].split("-")
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{int(d)} {months[int(mo) - 1]} {stamp[11:]}"


async def crash_report(runner) -> str:
    cores = await runner.run(["coredumpctl", "list", "--no-pager", "-r", "-n", "5"])
    journal = await runner.run(["journalctl", "-p", "err", "-b", "--no-pager", "-n", "80", "-o", "short-iso"])
    if cores.missing and journal.missing:
        return "The crash tools (coredumpctl, journalctl) are not available here."
    parts = []
    rows = parse_coredumps(cores.out) if not cores.missing else []
    if rows:
        parts.append("Recent crashes: " + " ".join(
            f"{os.path.basename(r.exe)} crashed on {_when(r.when)} with {r.signal}, {SIGNALS.get(r.signal, 'an unknown error')}"
            f"{' (a core file is kept)' if r.has_core else ''}." for r in rows[:4]))
    else:
        parts.append("No crashes recorded.")
    summary = summarise_journal(journal.out) if not journal.missing else []
    if summary:
        parts.append("Errors in the log since boot: " + "; ".join(
            f"{r.program} {r.count} time{'s' * (r.count != 1)} (for example: {r.sample})" for r in summary[:4]) + ".")
    elif not journal.missing:
        parts.append("The log shows no errors since boot.")
    parts.append("I only read these; nothing was changed.")
    return " ".join(parts)


def valid_unit(name: str) -> str:
    name = str(name or "").strip()
    if not _UNIT.fullmatch(name) or name[0] in "-.":
        raise ValueError("That is not a service name.")
    return name if name.endswith(_SUFFIXES) else name + ".service"


def _fields(out: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in str(out).splitlines() if "=" in line)


async def service_report(runner, name: str, user: bool = False) -> str:
    base = ["systemctl", "--user"] if user else ["systemctl"]
    if not str(name or "").strip():
        res = await runner.run([*base, "--failed", "--no-legend", "--plain"])
        if res.missing:
            return "systemctl is not available here."
        lines = [ln.split()[0] for ln in res.out.splitlines() if ln.strip()]
        if not lines:
            return "No services have failed."
        return f"{len(lines)} service{'s have' if len(lines) != 1 else ' has'} failed: " + ", ".join(lines[:10]) + \
            ". Ask me about one by name."
    unit = valid_unit(name)
    shown = await runner.run([*base, "show", unit, "-p", "ActiveState,SubState,Result,ExecMainStatus,NRestarts"])
    if shown.missing:
        return "systemctl is not available here."
    f = _fields(shown.out)
    log = await runner.run(["journalctl", "--user-unit" if user else "-u", unit, "-n", "15", "--no-pager", "-o", "cat"])
    text = log.out if not log.missing else ""
    state = f.get("ActiveState", "unknown")
    parts = [f"{unit} is {state}" + (f" ({f['Result']})" if f.get("Result") not in (None, "", "success") else "") + "."]
    if f.get("NRestarts", "0") not in ("", "0"):
        parts.append(f"It has restarted {f['NRestarts']} times.")
    cause = next((why for rx, why in CAUSES if re.search(rx, text, re.I)), "")
    if state in ("failed", "inactive") and cause:
        parts.append(f"Likely cause: {cause}.")
    tail = [scrub(ln, 100) for ln in text.splitlines() if ln.strip()][-2:]
    if tail:
        parts.append("Last log lines: " + " / ".join(tail))
    sudo = "" if user else "sudo "
    ctl = "systemctl --user" if user else "systemctl"
    parts.append(f"I did not change anything. To restart it yourself: {sudo}{ctl} restart {unit}. "
                 f"For more: journalctl {'--user-unit' if user else '-u'} {unit} -n 50.")
    return " ".join(parts)
