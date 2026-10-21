"""Everything the vault does, built from one folder and the switches: one object the plugin, the loops, the
viewer and Telegram share. Nothing touches the disk until `vault.enabled` is on and something asks for it."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from minty_core.vault.bookmarks import Bookmarks
from minty_core.vault.daily import DailyNote
from minty_core.vault.decisions import Decisions
from minty_core.vault.gate import hybrid_ranker, load_result
from minty_core.vault.glossary import Glossary
from minty_core.vault.pipeline import InboxPipeline
from minty_core.vault.private import PrivateArea
from minty_core.vault.resurface import Resurfacer
from minty_core.vault.search import Searcher
from minty_core.vault.store import Vault
from minty_core.vault.topics import Topics

OFF = "The notes vault is switched off (vault.enabled on the Switches page)."


class VaultOff(RuntimeError):
    pass


class VaultKit:
    def __init__(self, vault: Vault, gists, stale_months: int, clock: Callable[[], float], hybrid: bool = False,
                 private_lock_s: float = 600.0):
        self.vault = vault
        meta = vault.root / "_minty"
        self.gate_path = meta / "gate.json"
        self.search = Searcher(vault, gists, clock, stale_days=int(stale_months * 30.4))
        self._hybrid_asked = hybrid
        if hybrid and self.hybrid_state() == "on":
            self.search.ranker = hybrid_ranker(vault)
        self.decisions = Decisions(vault)
        self.bookmarks = Bookmarks(vault)
        self.glossary = Glossary(meta / "glossary.md")
        self.pipeline = InboxPipeline(vault, meta / "proposals.json", clock)
        self.daily = DailyNote(vault, clock)
        self.resurface = Resurfacer(vault, meta / "resurface.json", clock)
        self.topics = Topics(vault, gists, meta / "topics.json")
        self.private = PrivateArea(vault.root, clock, auto_lock_s=private_lock_s)

    def hybrid_state(self) -> str:
        """'on', or 'off: <why>'. Switched on in settings is not enough: the gate must have passed on your notes."""
        if not self._hybrid_asked:
            return "off: not switched on"
        result = load_result(self.gate_path)
        if result is None:
            return "off: the evaluation gate has not been run on your notes (minty notes gate questions.json)"
        if not result.passed:
            return f"off: the gate did not pass ({result.reason})"
        return "on"

    def evening(self, today) -> list[str]:
        """Things worth one line in the evening: new filing suggestions and decisions due for a second look.
        Each decision is mentioned once a day by the caller; suggestions only when they are new."""
        out = []
        new = self.pipeline.propose()
        if new:
            out.append(f"{len(new)} note{'s' * (len(new) != 1)} in your inbox ha{'ve' if len(new) != 1 else 's'} "
                       "filing suggestions.")
        for n in self.decisions.due(today)[:2]:
            out.append(f"The decision \"{n.title}\" is due for a second look.")
        return out


def _gate_stamp(root: Path) -> float:
    try:
        return (root / "_minty" / "gate.json").stat().st_mtime
    except OSError:
        return 0.0


def vault_root(path: str, home: Path | None = None) -> Path:
    return Path(path).expanduser() if path else (home or Path.home()) / ".minty" / "brain"


def glossary_hint(provider, cfg) -> str:
    """Your glossary words as speech-recogniser hints; empty unless the vault and the hints are switched on."""
    if not (cfg.vault.enabled and cfg.vault.glossary_hints):
        return ""
    try:
        return provider.kit().glossary.stt_hint()
    except Exception:  # noqa: BLE001 — a broken notes folder must never stop the microphone
        return ""


def memo_to_inbox(provider, text: str) -> str | None:
    """Save your own voice memo as a note in the inbox, or None when the vault is off (the caller falls back)."""
    if provider is None or not provider.active():
        return None
    try:
        note = provider.kit().vault.create(" ".join(text.split())[:60] or "Voice memo", text, folder="inbox",
                                           origin="user", source="telegram voice memo")
    except Exception:  # noqa: BLE001
        return None
    return f"Saved to your inbox (note:{note.id})."


def capture_note(provider, kind: str, text: str) -> str | None:
    """/note, /todo and /idea from the phone: a note in the inbox, or None when the vault is off."""
    if provider is None or not provider.active():
        return None
    text = " ".join(str(text).split())
    try:
        note = provider.kit().vault.create(text[:60], f"- [ ] {text}" if kind == "todo" else text, folder="inbox",
                                           origin="user", tags=[] if kind == "note" else [kind], source="telegram")
    except Exception:  # noqa: BLE001 — the caller falls back to the journal
        return None
    return f"Saved to your inbox (note:{note.id})."


def find_text(provider, query: str) -> str:
    from minty_core.vault.search import render

    if provider is None or not provider.active():
        return OFF
    hits = provider.kit().search.where(query, limit=5)
    return render(hits) if hits else "I can't find that in your notes or our conversations."


class VaultProvider:
    def __init__(self, config: Callable, gists: Callable = lambda: None, clock: Callable[[], float] = time.time,
                 home: Path | None = None):
        self._config, self._gists, self._clock, self._home = config, gists, clock, home
        self._kit: VaultKit | None = None
        self._key: tuple | None = None

    def config(self):
        return self._config()

    def active(self) -> bool:
        return bool(self._config().vault.enabled)

    def kit(self) -> VaultKit:
        cfg = self._config().vault
        if not cfg.enabled:
            raise VaultOff(OFF)
        root = vault_root(cfg.path, self._home)
        key = (str(root), cfg.git, cfg.stale_months, cfg.hybrid, _gate_stamp(root), cfg.private_lock_minutes)
        if self._kit is None or key != self._key:
            held = self._kit.private if self._kit is not None and str(root) == self._key[0] else None
            self._kit = VaultKit(Vault(root, clock=self._clock, git=cfg.git), self._gists(), cfg.stale_months,
                                 self._clock, cfg.hybrid, cfg.private_lock_minutes * 60.0)
            if held is not None:  # a settings change must not lock you out mid-session
                held._auto = cfg.private_lock_minutes * 60.0
                self._kit.private = held
            self._key = key
        return self._kit
