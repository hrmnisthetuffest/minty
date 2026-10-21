"""Updating Minty from its GitHub repository.

* `check`: once a day (``update.check``) Minty asks GitHub for the newest release of ``update.repo`` and, if it is newer than the
  installed version, shows a notice once. Nothing else leaves the machine and nothing is installed.
* `minty update`: shows what is new and, after you say yes, moves this git checkout to the release tag (a fast-forward only,
  never over local changes), installs changed dependencies, checks that the new version starts, and rolls back if it does not.
* ``update.auto_install`` (off by default) does the same by itself when a newer release is found. Code from the internet then
  runs on your computer without you reading it, so turn it on only for a repository whose owner you trust completely.

Everything that touches the system goes through ``runner``, and the network through ``get``, so it is testable without either.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from minty_core import __version__
from minty_core.updates import prepare, run

log = logging.getLogger(__name__)

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(a|alpha|b|beta|rc)[.]?(\d+)?)?$", re.IGNORECASE)
_STAGE = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2}
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
NOTES_MAX = 600


def parse_version(text: str) -> tuple[int, int, int, int, int] | None:
    """(major, minor, patch, stage, number): alpha 0, beta 1, rc 2, a release 3. None if it is not a version."""
    m = _VERSION.match((text or "").strip())
    if not m:
        return None
    major, minor, patch, stage, number = m.groups()
    if stage is None:
        return int(major), int(minor), int(patch), 3, 0
    return int(major), int(minor), int(patch), _STAGE[stage.lower()], int(number or 0)


def newer(current: str, tag: str) -> bool:
    a, b = parse_version(current), parse_version(tag)
    return a is not None and b is not None and b > a


@dataclass(frozen=True)
class Release:
    tag: str
    url: str = ""
    notes: str = ""

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")


@dataclass
class UpdateResult:
    ok: bool
    message: str
    old: str = ""
    new: str = ""
    rolled_back: bool = False
    steps: list[str] = field(default_factory=list)


Get = Callable[[str], Awaitable[tuple[int, object]]]


async def github_get(url: str) -> tuple[int, object]:
    from minty_core.httpclient import get_client

    response = await get_client().get(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "minty-update-check"},
                                      timeout=10.0)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


async def latest_release(repo: str, get: Get = github_get) -> Release | None:
    """The newest published release (pre-releases count; drafts and tags that are not versions do not), or None."""
    if not _REPO.match(repo or "") or ".." in repo:
        return None
    try:
        status, data = await get(f"https://api.github.com/repos/{repo}/releases?per_page=30")
    except Exception:  # noqa: BLE001 - offline, DNS, a proxy: not an error worth more than a log line
        log.debug("update check failed", exc_info=True)
        return None
    if status != 200 or not isinstance(data, list):
        return None
    best: tuple | None = None
    pick: dict | None = None
    for item in data:
        if not isinstance(item, dict) or item.get("draft") or not isinstance(item.get("tag_name"), str):
            continue
        parsed = parse_version(item["tag_name"])
        if parsed is not None and (best is None or parsed > best):
            best, pick = parsed, item
    if pick is None:
        return None
    return Release(pick["tag_name"], str(pick.get("html_url") or ""), str(pick.get("body") or "").strip()[:NOTES_MAX])


# ---- how often ---------------------------------------------------------------------------------------------------
def _state_file(config_dir: Path) -> Path:
    return Path(config_dir) / "update-state" / "last-check.json"


def _read_state(config_dir: Path) -> dict:
    try:
        data = json.loads(_state_file(config_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def check_due(config_dir: Path, now: float, every_hours: float) -> bool:
    at = _read_state(config_dir).get("at")
    return not isinstance(at, (int, float)) or now - at >= every_hours * 3600


def record_check(config_dir: Path, now: float, tag: str = "", notified: str | None = None) -> None:
    state = _read_state(config_dir)
    state.update({"at": now, "tag": tag})
    if notified is not None:
        state["notified"] = notified
    path = _state_file(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


# ---- the update itself ------------------------------------------------------------------------------------------
def origin_matches(url: str, repo: str) -> bool:
    """True only for github.com/<repo> (https, ssh or scp style, with or without .git)."""
    text = (url or "").strip().lower().removesuffix("/").removesuffix(".git")
    wanted = repo.lower()
    return text in (f"https://github.com/{wanted}", f"git@github.com:{wanted}", f"ssh://git@github.com/{wanted}")


_ORIGIN = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def detect_repo(root: Path, runner: Callable = run) -> str:
    """"owner/name" from this checkout's origin when it points at github.com, else "". A clone of the published project updates
    from where it came from without any setting."""
    if not (Path(root) / ".git").exists():
        return ""
    code, url = runner(["git", "remote", "get-url", "origin"], Path(root))
    m = _ORIGIN.match(url.strip()) if code == 0 else None
    return m.group(1) if m else ""


def apply_update(root: Path, config_dir: Path, release: Release, repo: str, runner: Callable = run, python: str = sys.executable,
                 origin_ok: Callable[[str], bool] | None = None, verify_signed: bool = False) -> UpdateResult:
    """Move the checkout in `root` to `release`. Refuses rather than guesses; rolls back if the new version does not start."""
    root = Path(root)
    steps: list[str] = []
    tag = release.tag

    def refuse(message: str) -> UpdateResult:
        return UpdateResult(False, message, steps=steps)

    if not (root / ".git").exists():
        return refuse("This copy of Minty is not a git checkout, so it cannot update itself. Run install.sh again from a fresh clone "
                      "of the repository.")
    if parse_version(tag) is None:
        return refuse(f"{tag!r} is not a version tag.")
    code, url = runner(["git", "remote", "get-url", "origin"], root)
    if code != 0 or not (origin_ok(url.strip()) if origin_ok is not None else origin_matches(url, repo)):
        return refuse(f"The origin of this checkout ({url.strip() or 'none'}) is not github.com/{repo}, so I won't update from it.")
    code, status = runner(["git", "status", "--porcelain"], root)
    if code != 0 or status.strip():
        return refuse("You have local changes in this checkout. Commit or stash them first; I won't overwrite them.")
    code, out = runner(["git", "fetch", "--tags", "--prune", "origin"], root)
    steps.append("fetched")
    if code != 0:
        return refuse(f"Could not fetch from GitHub: {out.strip()[-200:]}")
    code, _ = runner(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"], root)
    if code != 0:
        return refuse(f"The release {tag} is not in the repository (yet).")
    if verify_signed and runner(["git", "verify-tag", tag], root)[0] != 0:
        return refuse(f"The tag {tag} is not signed by a key I know, and update.verify_signed is on.")
    code, _ = runner(["git", "merge-base", "--is-ancestor", "HEAD", f"refs/tags/{tag}"], root)
    if code != 0:
        return refuse("This checkout has its own changes that are not in the release, so I won't overwrite it. "
                      "Merge the release yourself, or use a fresh clone.")
    try:
        state = prepare(root, config_dir, runner)
    except RuntimeError as e:
        return refuse(str(e))
    old = state["commit"]
    code, changed = runner(["git", "diff", "--name-only", old, f"refs/tags/{tag}", "--", "pyproject.toml"], root)
    deps_changed = bool(changed.strip())
    code, out = runner(["git", "merge", "--ff-only", f"refs/tags/{tag}"], root)
    if code != 0:
        return refuse(f"Could not move to {tag}: {out.strip()[-200:]}")
    steps.append("moved")

    def roll_back(why: str) -> UpdateResult:
        runner(["git", "reset", "--hard", old], root)
        if deps_changed:
            runner([python, "-m", "pip", "install", "-q", "-e", "."], root)
        return UpdateResult(False, f"{why} I went back to the version that worked.", old=old[:9], new=tag, rolled_back=True, steps=steps)

    if deps_changed:
        code, out = runner([python, "-m", "pip", "install", "-q", "-e", "."], root)
        steps.append("dependencies")
        if code != 0:
            return roll_back(f"Installing the dependencies failed ({out.strip()[-160:]}).")
    code, out = runner([python, "-m", "minty_core", "--version"], root / "src")
    steps.append("checked")
    if code != 0 or not re.search(rf"(?<![\w.]){re.escape(release.version)}(?![\w.])", out):  # exactly that version, not 1.0.12 for 1.0.1
        return roll_back(f"The new version did not start ({out.strip()[-160:] or 'no output'}).")
    return UpdateResult(True, f"Updated to {tag}. It starts using it the next time Minty restarts.", old=old[:9], new=tag, steps=steps)


# ---- the command and the background check ------------------------------------------------------------------------
def _ask(prompt: str) -> bool:
    return input(prompt).strip().lower() in ("y", "yes")


async def run_cli(args: list[str], *, root: Path, config_dir: Path, repo: str, get: Get = github_get, current: str = __version__,
                  ask: Callable[[str], bool] = _ask, runner: Callable = run, python: str = sys.executable,
                  origin_ok: Callable[[str], bool] | None = None, verify_signed: bool = False) -> int:
    """`minty update [--check] [--yes]`. Exit 0: up to date or updated. 1: not updated. 2: not set up. 10 (--check): newer exists."""
    if not repo:
        print("Set update.repo to the GitHub repository, for example update.repo = \"owner/minty\" in config.toml (or the panel).")
        return 2
    release = await latest_release(repo, get)
    record_check(config_dir, time.time(), release.tag if release else "")
    if release is None:
        print(f"I could not find a release of github.com/{repo} (offline, or none published yet).")
        return 1
    if not newer(current, release.tag):
        print(f"Minty {current} is up to date (the newest release is {release.tag}).")
        return 0
    print(f"Minty {release.version} is available (you have {current}).")
    if release.notes:
        print(release.notes)
    if release.url:
        print(release.url)
    if "--check" in args:
        print("Run `minty update` to install it.")
        return 10
    if "--yes" not in args and not ask(f"Install {release.tag}? [y/N] "):
        print("Not installed.")
        return 1
    result = await asyncio.to_thread(apply_update, root, config_dir, release, repo, runner, python, origin_ok, verify_signed)
    print(result.message)
    return 0 if result.ok else 1


async def background_check(cfg, config_dir: Path, publish: Callable[..., None], *, root: Path, now: float | None = None,
                           get: Get = github_get, current: str = __version__, apply: Callable = apply_update,
                           runner: Callable = run, python: str = sys.executable) -> Release | None:
    """The daily check: tell once about a newer release (display notice only, never spoken), or install it if update.auto_install."""
    if not cfg.check or not cfg.repo:
        return None
    now = time.time() if now is None else now
    if not check_due(config_dir, now, cfg.check_every_hours):
        return None
    release = await latest_release(cfg.repo, get)
    state = _read_state(config_dir)
    record_check(config_dir, now, release.tag if release else "")
    if release is None or not newer(current, release.tag) or state.get("notified") == release.tag:
        return None
    if cfg.auto_install:
        result = await asyncio.to_thread(apply, root, config_dir, release, cfg.repo, runner, python, None, cfg.verify_signed)
        publish("notice", message=result.message)
    else:
        publish("notice", message=f"Minty {release.version} is available. Run `minty update` to install it.")
    record_check(config_dir, now, release.tag, notified=release.tag)
    return release
