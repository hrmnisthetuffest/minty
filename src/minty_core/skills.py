"""Skills in the open agentskills.io format: a folder with a SKILL.md (frontmatter + instructions).

Nothing is in the prompt until a request matches a skill; then its body is added to the end of that turn's
system prompt and the tool groups it names are switched on. Adding a skill is dropping a folder into
`~/.config/minty/skills/` (or the package's `skills_builtin/`); a user skill with the same name wins.
Skills written for Claude Code or Hermes load as they are.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

log = logging.getLogger(__name__)

BUILTIN_SKILLS_DIR = Path(__file__).parent / "skills_builtin"
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_NAME, MAX_DESCRIPTION, MAX_BODY_LINES, MAX_BODY_CHARS = 64, 1024, 500, 4800  # ~1 200 tokens in a prompt
RESCAN_S = 2.0  # a new or edited SKILL.md is noticed within this many seconds
MIN_MATCH = 2  # score a request needs before a skill is loaded for it
_STOP = set("""a an the to for of and or in on at it is are was be do does me my i you your we our this that these those
with from by as so if then than can could would should will just please some any about up out into over make get
want need like let know""".split())
_WORD = re.compile(r"[a-z0-9]+")


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    allowed_tools: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)
    source: str = ""

    @cached_property
    def _description_words(self) -> frozenset:
        return frozenset(_content_words(self.description))

    def key_words(self) -> set[str]:
        chosen = str(self.metadata.get("keywords", "")).strip()
        return _content_words(chosen or self.name.replace("-", " "))

    def score(self, request_words: set[str]) -> int:
        """Words the author chose (name, keywords) count double; the free-text description adds at most one,
        so an example product in a description cannot pull a request into the wrong skill."""
        keys = self.key_words()
        return 2 * len(request_words & keys) + min(1, len(request_words & (self._description_words - keys)))


def _stem(word: str) -> str:
    for suffix in ("ing", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _content_words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall((text or "").lower().replace("'", "")) if w not in _STOP and len(w) > 1}


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """The YAML subset skills use: scalars, quoted strings, `>`/`|` blocks, and one level of nested mapping."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with --- frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise SkillError("frontmatter is not closed with ---") from None
    data: dict = {}
    i = 1
    while i < end:
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0] in " \t" or ":" not in raw:
            raise SkillError(f"cannot read frontmatter line: {raw.strip()[:60]!r}")
        key, _, value = raw.partition(":")
        key, value = key.strip(), value.strip()
        block = []
        while i < end and (lines[i][:1] in (" ", "\t") or not lines[i].strip()):
            block.append(lines[i])
            i += 1
        if value in (">", "|", ">-", "|-"):
            sep = " " if value.startswith(">") else "\n"
            data[key] = sep.join(b.strip() for b in block if b.strip())
        elif value:
            data[key] = _scalar(value)
        elif block:
            nested = {}
            for b in block:
                if b.strip() and ":" in b:
                    k, _, v = b.partition(":")
                    nested[k.strip()] = _scalar(v)
            data[key] = nested
        else:
            data[key] = ""
    return data, "\n".join(lines[end + 1:]).strip()


def load_skill(folder: Path) -> Skill:
    path = Path(folder) / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SkillError(f"cannot read {path}: {e}") from e
    meta, body = parse_frontmatter(text)
    name, description = str(meta.get("name") or ""), str(meta.get("description") or "").strip()
    if not NAME_RE.fullmatch(name) or len(name) > MAX_NAME:
        raise SkillError(f"name {name!r} must be lowercase letters, digits and hyphens, at most {MAX_NAME}")
    if name != Path(folder).name:
        raise SkillError(f"name {name!r} must equal its folder name {Path(folder).name!r}")
    if not description or len(description) > MAX_DESCRIPTION:
        raise SkillError(f"description must be 1 to {MAX_DESCRIPTION} characters")
    if not body:
        raise SkillError("a skill needs instructions after the frontmatter")
    if len(body.splitlines()) > MAX_BODY_LINES:
        raise SkillError(f"instructions are limited to {MAX_BODY_LINES} lines")
    allowed = tuple(str(meta.get("allowed-tools") or "").replace(",", " ").split())
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    return Skill(name, description, body, allowed, metadata, str(path))


class SkillIndex:
    """All skills from the given folders (later folders win), re-read when a SKILL.md changes."""

    def __init__(self, dirs):
        self._dirs = [Path(d) for d in dirs]
        self._sig: tuple = ()
        self._skills: dict[str, Skill] = {}
        self._checked = -1e9  # when the folders were last looked at; looking costs a directory scan
        self._memo: tuple[str | None, int, Skill | None] = (None, 0, None)
        self._generation = 0
        self.problems: list[str] = []

    def _files(self) -> list[Path]:
        return [p for d in self._dirs if d.is_dir() for p in sorted(d.glob("*/SKILL.md"))]

    def skills(self) -> dict[str, Skill]:
        now = time.monotonic()
        if now - self._checked < RESCAN_S:
            return self._skills
        self._checked = now
        files = self._files()
        sig = tuple((str(p), p.stat().st_mtime_ns) for p in files if p.exists())
        if sig != self._sig:
            self._sig, self._skills, self.problems = sig, {}, []
            self._generation += 1
            for path in files:
                try:
                    skill = load_skill(path.parent)
                except SkillError as e:
                    log.warning("skill %s skipped: %s", path.parent.name, e)
                    self.problems.append(f"{path.parent.name}: {e}")
                    continue
                self._skills[skill.name] = skill
        return self._skills

    def match(self, request: str) -> Skill | None:
        skills = self.skills()
        if self._memo[0] == request and self._memo[1] == self._generation:
            return self._memo[2]
        found = self._match(request, skills)
        self._memo = (request, self._generation, found)
        return found

    def _match(self, request: str, skills: dict) -> Skill | None:
        words = _content_words(request)
        best, best_score = None, MIN_MATCH - 1
        for skill in skills.values():
            score = skill.score(words)
            if score > best_score:
                best, best_score = skill, score
        return best

    def section(self, request: str) -> str:
        """The text appended to this turn's system prompt, or '' when no skill matches."""
        skill = self.match(request)
        if skill is None:
            return ""
        return f"\n\n## Skill: {skill.name}\n{skill.body[:MAX_BODY_CHARS]}"
