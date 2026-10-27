"""The checks that run before a tool does anything, in one place.

`SafetyKit.precheck` is called by the agent for every tool call, before the confirmation gate (`policy.Guard`). It can
only block: it never allows something the gate would refuse. Each check is a setting under `safety.*`.
"""
from __future__ import annotations

import logging
from typing import Callable

from minty_core.lockdown import allowed_during_lockdown
from minty_core.policy import classify
from minty_core.secretscan import SecretScanner
from minty_core.toollimits import ToolLimits

log = logging.getLogger(__name__)

# plugins whose tools reach the internet; privacy mode refuses them (local-network home control is not here)
NETWORK_PLUGINS = ("apis", "browser", "research", "mail", "n8n", "school", "spotify", "weather", "web_search",
                   "installer", "music_id", "briefing")
NETWORK_TOOLS = frozenset({"files_download", "files_upload"})


class SafetyKit:
    def __init__(self, store, scanner: SecretScanner, tracer=None, notify: Callable[[str], None] | None = None):
        self._store = store
        self.scanner = scanner
        self._tracer = tracer
        self._notify = notify
        self.alarms: list[Callable[[str], None]] = []  # called with a reason when something serious is seen
        self.limits = ToolLimits(store)
        self.lockdown = None  # lockdown.Lockdown

    # ---- the one entry point -------------------------------------------------------------------
    def precheck(self, name: str, args: dict | None, tool=None) -> str | None:
        """A reason (spoken as-is) when the call must not run, else None."""
        cfg = self._store.config.safety
        if self.lockdown is not None and self.lockdown.active and not allowed_during_lockdown(name):
            return "lockdown is on, so nothing changes or goes out until you say end lockdown"
        if cfg.privacy_mode and (name in NETWORK_TOOLS or name.startswith(tuple(p + "_" for p in NETWORK_PLUGINS))
                                 or getattr(tool, "egress", None) is not None):
            return "privacy mode is on, so nothing is sent to the internet until you turn it off"
        blocker = getattr(tool, "blocked_when", None)
        if blocker is not None:  # the tool's own refusal (switched off, a command it will not run), before anyone is asked
            try:
                own = blocker(args or {})
            except Exception:  # noqa: BLE001 — a check that cannot be made is a refusal
                log.warning("blocked_when of %s failed; refusing", name, exc_info=True)
                own = "its own safety check could not be made"
            if own:
                return own
        if cfg.block_secret_egress:
            reason = self._secrets_in(name, args)
            if reason:
                return reason
        risk = classify(tool, args or {}) if tool is not None else 0
        reason = self.limits.check(name, risk)
        if reason:
            if self._tracer is not None:
                self._tracer.event("tool_limit", tool=name, usage=self.limits.usage())
            return reason
        return None

    async def advise(self, name: str, args: dict | None) -> str:
        """A sentence to add to the confirmation question (for installs: what is publicly known about the package)."""
        if name != "installer_install" or not self._store.config.safety.audit_installs:
            return ""
        from minty_core.depaudit import audit, note
        args = args or {}
        try:
            return note(await audit(str(args.get("manager", "pip")).lower(), str(args.get("package", ""))))
        except Exception:  # noqa: BLE001 — advice must never block the question itself
            log.warning("dependency audit failed", exc_info=True)
            return ""

    def executing(self, name: str, args: dict | None, tool=None) -> None:
        """The call passed every check and is about to run: count it."""
        self.limits.record(name, classify(tool, args or {}) if tool is not None else 0)

    # ---- secrets and honeytokens ---------------------------------------------------------------
    def _secrets_in(self, name: str, args) -> str | None:
        found = [f for f in self.scanner.scan_args(args or {}) if f.severity in ("secret", "alarm")]
        if not found:
            return None
        alarm = any(f.severity == "alarm" for f in found)
        where = found[0].where or "the request"
        if self._tracer is not None:
            self._tracer.event("secret_blocked", tool=name, kinds=sorted({f.kind for f in found}), where=where,
                               alarm=alarm)  # the value itself is never written
        if alarm:
            self._raise_alarm(f"{name} was about to send a planted fake key; I stopped it.")
            return "a planted fake credential was about to be sent out; that means something went looking for keys"
        return f"it would send {found[0].label} ({where}); secrets never leave in a tool call"

    def _raise_alarm(self, message: str) -> None:
        log.warning("safety alarm: %s", message)
        if self._notify is not None:
            try:
                self._notify(message)
            except Exception:  # noqa: BLE001
                log.warning("alarm notification failed", exc_info=True)
        for hook in self.alarms:
            try:
                hook(message)
            except Exception:  # noqa: BLE001
                log.warning("alarm hook failed", exc_info=True)
