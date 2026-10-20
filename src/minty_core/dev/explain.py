"""Command explainer with danger warnings (652). Pure text analysis: it splits a command into parts, says in plain words what
each program and flag does, and warns about what can destroy data. It never runs, imports or evaluates anything."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

MAX_CHARS = 2000
RANK = {"safe": 0, "careful": 1, "danger": 2}
PROGRAMS = {
    "ls": "lists files", "cd": "changes the current folder", "cat": "shows a file", "grep": "searches text for a pattern",
    "find": "searches for files", "rm": "deletes files", "cp": "copies files", "mv": "moves or renames files",
    "mkdir": "makes folders", "chmod": "changes who may read, write or run a file", "chown": "changes who owns a file",
    "sudo": "runs the next command with administrator rights", "tar": "packs and unpacks archives",
    "curl": "fetches something from the internet", "wget": "downloads from the internet", "git": "version control",
    "echo": "prints text", "printf": "prints text", "pwd": "shows the current folder", "head": "shows the start of a file",
    "tail": "shows the end of a file", "sort": "sorts lines", "wc": "counts lines, words or bytes", "ps": "lists programs running",
    "kill": "stops a program by number", "killall": "stops programs by name", "pkill": "stops programs by name",
    "df": "shows free disk space", "du": "shows how much space files use", "dd": "copies raw data, often to or from disks",
    "pacman": "the Arch package manager", "apt": "the Debian/Mint package manager", "pip": "installs Python packages",
    "npm": "installs JavaScript packages", "systemctl": "controls system services", "shutdown": "turns the computer off",
    "reboot": "restarts the computer", "poweroff": "turns the computer off", "ssh": "logs in to another computer",
    "scp": "copies files to another computer", "rsync": "copies and syncs folders", "docker": "runs containers",
    "python": "runs a Python program", "python3": "runs a Python program", "node": "runs a JavaScript program",
    "touch": "makes an empty file or updates its time", "less": "reads a file page by page", "man": "shows a manual page",
    "mount": "attaches a disk", "umount": "detaches a disk", "ln": "makes a link", "sed": "edits text in a stream",
    "awk": "processes text by columns", "xargs": "runs a command on each input line", "bash": "runs a shell",
    "sh": "runs a shell", "export": "sets an environment variable", "which": "shows where a program is",
}
FLAGS = {
    "ls": {"l": "long format", "a": "includes hidden files", "h": "readable sizes", "R": "all subfolders", "t": "newest first"},
    "tar": {"x": "extract", "c": "create an archive", "z": "gzip", "j": "bzip2", "J": "xz", "v": "show files as it goes",
            "f": "use the named file", "t": "list the contents"},
    "rm": {"r": "folders and everything inside", "R": "folders and everything inside", "f": "no questions, ignores missing files",
           "i": "asks before each file", "v": "shows what it removes"},
    "cp": {"r": "folders too", "R": "folders too", "v": "shows what it copies", "i": "asks before overwriting",
           "a": "keeps permissions and links"},
    "grep": {"i": "ignores case", "r": "searches folders", "R": "searches folders", "n": "shows line numbers",
             "v": "shows lines that do not match", "l": "lists matching files only"},
    "mkdir": {"p": "makes parent folders too"}, "chmod": {"R": "everything inside too"}, "chown": {"R": "everything inside too"},
    "mv": {"i": "asks before overwriting", "v": "shows what it moves"}, "du": {"h": "readable sizes", "s": "totals only"},
    "df": {"h": "readable sizes"}, "ps": {"a": "all users", "u": "with owners", "x": "without a terminal", "e": "every program",
                                         "f": "full format"},
    "head": {"n": "number of lines"}, "tail": {"n": "number of lines", "f": "keeps following new lines"},
    "curl": {"s": "quiet", "L": "follows redirects", "o": "saves to a file", "O": "saves with the remote name"},
    "wget": {"q": "quiet", "O": "saves to a file"},
}
SYSTEM_TARGETS = {"/", "/*", "~", "~/", "~/*", "$HOME", "$HOME/", "$HOME/*", "*", ".", "..", "/home", "/etc", "/usr", "/var",
                  "/boot", "/bin", "/lib", "/sbin", "/opt", "/root"}
NOT_A_PROGRAM = {"sudo", "doas", "env", "nohup", "time", "command", "exec"}
_FORK = re.compile(r":\s*\(\s*\)\s*\{.*\};\s*:")
_PIPE_SHELL = re.compile(r"(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b")
_RAW_DEVICE = re.compile(r">\s*/dev/(sd|nvme|hd|mmcblk|vd)")


@dataclass
class Explanation:
    level: str = "safe"
    parts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    count: int = 0

    def raise_to(self, level: str) -> None:
        if RANK[level] > RANK[self.level]:
            self.level = level

    def text(self) -> str:
        if not self.count:
            return "Nothing to explain."
        head = [f"This is {self.count} commands, one after the other:"] if self.count > 1 else []
        word = {"safe": "Nothing in it looks dangerous, but I only judge the text, not what the files hold.",
                "careful": "Be careful:", "danger": "DANGER:"}[self.level]
        tail = [word] + (self.warnings if self.level != "safe" else [])
        return "\n".join(head + self.parts + tail)[:1400]


def _split(text: str) -> list[str]:
    """Split on unquoted && || ; | & and newlines."""
    parts, cur, quote, i = [], [], "", 0
    while i < len(text):
        c = text[i]
        if quote:
            cur.append(c)
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "\\" and i + 1 < len(text):
            cur.append(text[i:i + 2])
            i += 1
        elif c in ";\n|&":
            if i + 1 < len(text) and text[i + 1] == c and c in "|&":
                i += 1
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _substitutions(text: str) -> list[str]:
    found, i = [], 0
    while True:
        i = text.find("$(", i)
        if i < 0:
            break
        depth, j = 1, i + 2
        while j < len(text) and depth:
            depth += (text[j] == "(") - (text[j] == ")")
            j += 1
        found.append(text[i + 2:j - 1])
        i = j
    found += re.findall(r"`([^`]*)`", text)
    return found


def _flags(prog: str, args: list[str]) -> tuple[list[str], list[str], set[str]]:
    notes, targets, letters = [], [], set()
    table = FLAGS.get(prog, {})
    for a in args:
        if a.startswith("--") and len(a) > 2:
            letters.add(a)
        elif a.startswith("-") and len(a) > 1 and not a[1].isdigit():
            for ch in a[1:]:
                letters.add(ch)
                if ch in table:
                    notes.append(f"-{ch}: {table[ch]}")
        else:
            targets.append(a)
    return notes, targets, letters


def _assess(prog: str, args: list[str], sudo: bool, out: Explanation) -> None:
    notes, targets, flags = _flags(prog, args)
    clean = [t.rstrip("/") or "/" for t in targets]
    if prog == "rm":
        recursive = bool(flags & {"r", "R", "--recursive"})
        if "--no-preserve-root" in flags or any(t in SYSTEM_TARGETS for t in clean):
            out.raise_to("danger")
            where = next((t for t in targets if t.rstrip("/") in SYSTEM_TARGETS or t in SYSTEM_TARGETS), "everywhere")
            out.warnings.append(f"It deletes everything in {where}, and there is no Trash. Do not run this.")
        elif recursive:
            out.raise_to("careful")
            out.warnings.append("It deletes the folder and everything inside it for good; there is no Trash.")
        else:
            out.raise_to("careful")
            out.warnings.append("It deletes for good; there is no Trash.")
    elif prog == "dd":
        if any(a.startswith("of=/dev/") for a in args):
            out.raise_to("danger")
            out.warnings.append("It writes raw data straight onto a disk, destroying what is on it. Check of= twice.")
        else:
            out.raise_to("careful")
            out.warnings.append("dd copies raw data; a wrong of= can wipe a disk.")
    elif prog.startswith("mkfs") or prog in ("wipefs", "fdisk", "parted", "sgdisk", "shred"):
        out.raise_to("danger")
        out.warnings.append("It formats or edits a disk, destroying everything on it.")
    elif prog in ("chmod", "chown"):
        if "R" in flags and any(t in SYSTEM_TARGETS for t in clean):
            out.raise_to("danger")
            out.warnings.append("It changes permissions on the whole system and can leave it unusable.")
        elif "R" in flags and "777" in args:
            out.raise_to("careful")
            out.warnings.append("777 lets anyone change these files; that is rarely what you want.")
    elif prog == "git" and args:
        sub, rest = args[0], set(args[1:])
        if sub == "push" and rest & {"--force", "-f"}:
            out.raise_to("careful")
            out.warnings.append("A force push overwrites what others have on the server.")
        elif sub == "reset" and "--hard" in rest:
            out.raise_to("careful")
            out.warnings.append("reset --hard throws away your uncommitted changes.")
        elif sub == "clean" and (rest & {"-f", "-fd", "-fdx", "-df", "-xdf"}):
            out.raise_to("careful")
            out.warnings.append("git clean deletes untracked files for good.")
        elif sub == "branch" and rest & {"-D", "-d"}:
            out.raise_to("careful")
            out.warnings.append("It deletes a branch.")
    elif prog == "pacman":
        joined = " ".join(args)
        if re.search(r"-\w*S\w*u", joined) or "-Syu" in joined:
            out.raise_to("careful")
            out.warnings.append("It updates the whole system. This is the normal way on Arch; read the Arch news first.")
        elif re.search(r"-\w*Sy\b", joined) or joined.strip() == "-Sy":
            out.raise_to("careful")
            out.warnings.append("-Sy without -u is a partial upgrade, which can break Arch. Use -Syu.")
        elif re.search(r"-\w*R", joined):
            out.raise_to("careful")
            out.warnings.append("It removes packages" + (" without checking what needs them (-dd)." if "dd" in joined else "."))
        elif re.search(r"-\w*S", joined):
            out.raise_to("careful")
            out.warnings.append("It installs software with administrator rights.")
    elif prog == "apt" and args and args[0] in ("install", "remove", "purge", "upgrade", "full-upgrade", "autoremove"):
        out.raise_to("careful")
        out.warnings.append("It changes installed software.")
    elif prog in ("shutdown", "reboot", "poweroff", "halt"):
        out.raise_to("careful")
        out.warnings.append("It turns off or restarts the computer; unsaved work is lost.")
    elif prog in ("kill", "killall", "pkill"):
        out.raise_to("careful")
        out.warnings.append("It stops programs; unsaved work in them is lost.")
    elif prog == "systemctl" and args and args[0] in ("stop", "disable", "mask", "restart"):
        out.raise_to("careful")
        out.warnings.append("It changes a running service.")
    elif prog in ("pip", "pip3", "npm") and args and args[0] in ("install", "i"):
        out.raise_to("careful")
        out.warnings.append("It downloads and installs code from the internet.")
    elif prog == "find" and ({"-delete"} & set(args) or "-exec" in args):
        out.raise_to("careful")
        out.warnings.append("find can delete or run commands on everything it matches.")
    if sudo:
        out.raise_to("careful")
        out.warnings.append("It runs with administrator rights, so a mistake can reach system files.")


def _redirects(segment: str, out: Explanation) -> None:
    for m in re.finditer(r"(?<![>0-9&])>\s*([^\s|;&]+)", segment):
        target = m.group(1)
        if target.startswith("/dev/") and not target.startswith(("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty")):
            out.raise_to("danger")
            out.warnings.append("It writes straight onto a device, which can destroy a disk.")
        elif not target.startswith("/dev/"):
            out.raise_to("careful")
            out.warnings.append(f"> overwrites {target[:40]} if it exists (>> would add to it instead).")


def explain_command(text: str, _depth: int = 0) -> Explanation:
    text = str(text or "")[:MAX_CHARS].strip()
    out = Explanation()
    if not text:
        return out
    if _FORK.search(text):
        out.raise_to("danger")
        out.warnings.append("This is a fork bomb: it starts programs endlessly until the computer freezes.")
    if _PIPE_SHELL.search(text):
        out.raise_to("danger")
        out.warnings.append("It runs a script downloaded from the internet without looking at it.")
        out.warnings.append("Safer: download it first, read it, then run it.")
    if _RAW_DEVICE.search(text):
        out.raise_to("danger")
        out.warnings.append("It writes straight onto a disk, destroying what is on it.")
    if _depth < 2:
        for inner in _substitutions(text):
            sub = explain_command(inner, _depth + 1)
            if sub.count:
                out.raise_to(sub.level)
                out.warnings += [f"Inside it, another command runs: {w}" for w in sub.warnings]
    segments = _split(text)
    out.count = len(segments)
    for seg in segments:
        _redirects(seg, out)
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            tokens = seg.split()
        sudo = False
        while tokens and (tokens[0] in NOT_A_PROGRAM or re.fullmatch(r"[A-Za-z_]\w*=\S*", tokens[0])):
            sudo = sudo or tokens[0] in ("sudo", "doas")
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-") and sudo and len(tokens[0]) <= 3 and tokens[0] != "--":
                tokens = tokens[1:]
        if not tokens:
            continue
        prog, args = tokens[0].rsplit("/", 1)[-1], tokens[1:]
        notes, targets, _ = _flags(prog, args)
        if prog in PROGRAMS or prog.startswith("mkfs"):
            line = f"• {prog}: {PROGRAMS.get(prog, 'formats a disk')}."
            if prog == "git" and args:
                line = f"• git {args[0]}: version control ({args[0]})."
            if notes:
                line += " " + "; ".join(notes[:5]) + "."
            if targets and prog not in ("git",):
                line += " On: " + ", ".join(t[:40] for t in targets[:3]) + "."
        else:
            line = f"• {prog[:40]}: I don't know this command. Read `man {prog[:40]}` before running it."
            out.raise_to("careful")
            out.warnings.append(f"I don't know {prog[:40]}, so I cannot say what it does.")
        out.parts.append(line)
        _assess(prog, args, sudo, out)
    return out
