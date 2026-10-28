"""What may run without asking. One decision path used everywhere tools run (conversation, browser agent,
live mode), so safety lives in code instead of in prompt wording.

Risk classes: 0 read · 1 reversible change · 2 visible to others or hard to undo · 3 money, credentials,
deletion, or personal data sent to a new party. Class 3 is always asked.
"""
import fnmatch
import logging
import re
import unicodedata
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

R0, R1, R2, R3 = 0, 1, 2, 3
RISK_NAMES = {R0: "read", R1: "change", R2: "visible", R3: "critical"}
ASK_FROM = {"careful": R1, "commits": R2, "trusted": R3}  # lowest class that is asked in each mode
DECISIONS = ("allow", "ask", "deny")
_SECRET_ARG = re.compile(r"key|token|secret|password|passwd|otp|pin|cvv", re.IGNORECASE)
_POSITIVE = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "proceed", "affirmative"}
_NEGATIVE = {"no", "nope", "don't", "dont", "stop", "cancel", "negative", "never",
             "not", "wait", "hold", "nah", "unsure"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", (text or "").lower().replace("’", "'")))


def is_affirmative(text: str) -> bool:
    """A clear yes with no no in it ("yes... actually no" is a no)."""
    words = _words(text)
    return not (words & _NEGATIVE) and bool(words & _POSITIVE)


def is_negative(text: str) -> bool:
    return bool(_words(text) & _NEGATIVE)


def classify(tool, args: dict) -> int:
    """The risk class of this call. Errors in a tool's own risk function count as class 2 (ask)."""
    risk = R0
    raw = getattr(tool, "risk", R0)
    try:
        risk = int(raw(args) if callable(raw) else raw)
    except Exception:
        log.warning("risk function of %s failed; treating as class 2", getattr(tool, "name", "?"), exc_info=True)
        risk = R2
    if getattr(tool, "risky", False):
        risk = max(risk, R2)
    confirm_when = getattr(tool, "confirm_when", None)
    if confirm_when is not None:
        try:
            if confirm_when(args):
                risk = max(risk, R2)
        except Exception:
            risk = max(risk, R2)
    return max(R0, min(R3, risk))


def subject(name: str, args: dict, described: str = "") -> str:
    """What rules are matched against: the arguments (secrets left out) plus the spoken description."""
    parts = [f"{k}={v}" for k, v in sorted((args or {}).items()) if not _SECRET_ARG.search(str(k))]
    return " ".join(parts + ([described] if described else []))


def decide(name: str, risk: int, mode: str = "commits", rules=(), text: str = "") -> str:
    for rule in rules or ():
        if not isinstance(rule, dict):
            continue
        match, decision = str(rule.get("match") or ""), rule.get("decision")
        if decision not in DECISIONS or not match:
            log.warning("ignoring malformed safety rule %r", rule)
            continue
        tool_glob, _, arg_glob = match.partition(":")
        if fnmatch.fnmatchcase(name, tool_glob) and (not arg_glob or fnmatch.fnmatch(text.lower(), arg_glob.lower())):
            if decision == "allow" and risk >= R3:
                return "ask"  # money, credentials and deletion are never silent
            return decision
    return "ask" if risk >= ASK_FROM.get(mode, R2) else "allow"


# ---- text the user hears or reads in a confirmation ------------------------------------------------
_HIDDEN = {"Cc", "Cf", "Co", "Cs"}  # control, format (zero-width, bidi overrides), private use, surrogates
_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']*[^\s<>\"'.,;:!?)]", re.IGNORECASE)  # not trailing punctuation
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu", "nic", "res", "gen", "firm", "ind"}


def registrable(host: str) -> str:
    if ":" in host or host.replace(".", "").isdigit():  # an IP address is shown whole
        return host
    labels = [l for l in host.lower().strip(".").split(".") if l]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def display_destination(url: str) -> str:
    """The part of a URL that matters for trust, with look-alike characters made visible."""
    parts = urlsplit(url if "://" in url else "https://" + url)
    host = parts.hostname or ""
    try:
        shown = host.encode("ascii").decode("idna") if "xn--" in host else host
    except UnicodeError:
        shown = host
    base = registrable(host)
    if shown != host or not host.isascii():
        return f"{registrable(shown)} (look-alike address {base})"
    return base or url


def safe_text(text, limit: int = 200) -> str:
    """Confirmation wording: normalised, hidden characters removed, links reduced to their real site."""
    s = unicodedata.normalize("NFKC", str(text or "")).replace("...", "…")
    s = "".join(ch for ch in s if unicodedata.category(ch) not in _HIDDEN or ch in "\t\n")
    s = " ".join(s.split())
    s = _URL.sub(lambda m: display_destination(m.group(0)), s)
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


# ---- where data goes ---------------------------------------------------------------------------------
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,24}\b", re.IGNORECASE)
# personal data in a link is how a hijacked agent leaks it ("…/collect?data=<your address>")
_PERSONAL = re.compile(r"[\w.+-]+(?:@|%40)[\w-]+\.|\b\d{10,}\b|address|phone|password|passwd|token|secret|"
                       r"street|road|nagar|%20[a-z]+%20", re.IGNORECASE)


def destination_key(dest: str) -> str:
    dest = (dest or "").strip().lower()
    if _EMAIL.fullmatch(dest):
        return dest
    host = urlsplit(dest if "://" in dest else "https://" + dest).hostname or dest
    return registrable(host)


def destinations_in(text: str) -> set[str]:
    """Sites and addresses mentioned in a piece of text (the user's own words count as permission)."""
    text = text or ""
    found = {e.lower() for e in _EMAIL.findall(text)}
    stripped = _EMAIL.sub(" ", text)
    found |= {destination_key(u) for u in _URL.findall(stripped)}
    found |= {destination_key(d) for d in _DOMAIN.findall(_URL.sub(" ", stripped))}
    return {f for f in found if f}


def link_carries_personal_data(url: str) -> bool:
    parts = urlsplit(url if "://" in url else "https://" + url)
    return bool(_PERSONAL.search(parts.query + " " + parts.fragment + " " + parts.path.rsplit("/", 1)[-1]))


def link_egress(field: str = "url"):
    """`egress` for tools that open or fetch a link: only a link carrying personal data sends anything out."""
    def egress(args: dict) -> str | None:
        url = str((args or {}).get(field) or "")
        return url if link_carries_personal_data(url) else None
    return egress


# ---- the one gate every tool call passes ---------------------------------------------------------------
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Awaitable, Callable  # noqa: E402


# ---- instruction is authorization -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Mandate:
    """What a tool's question is for, so the user's own words can answer it. A tool that carries one is not asked about when the
    user's current request (their words, never tool output) says this kind of action (`intent`, a regex), every argument in
    `targets` (who or where it touches) comes from those words, nothing the call uses came from elsewhere, and its class is
    not above `max_class`. Class 3 stays asked unless the tool names 3 here, which is for a cost (credits), never for money,
    keys, deletion or people. Deny rules and lockdown are checked first and never lifted."""
    intent: str
    targets: tuple[str, ...] = ()
    max_class: int = R2
    check: Callable[[dict, str], bool] | None = None  # (arguments, the user's words) -> False when this call goes beyond what was asked
    class3_flag: str = ""  # a call above class 2 is lifted only if this safety setting is on (it is off unless the user turned it on)


_ACK = frozenset({"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go", "ahead", "do", "it", "please", "proceed", "confirm", "that",
                  "this", "thanks", "thank", "you", "alright", "right", "fine", "good", "great", "correct", "now", "then", "so"})


def current_request(said: list[str]) -> str:
    """The user's latest real ask: "yes" and "go ahead" answer a question, they are not a new request."""
    for text in reversed(said or []):
        words = re.findall(r"[a-z']+", (text or "").lower())
        if words and not (len(words) <= 4 and all(w in _ACK for w in words)):
            return text
    return said[-1] if said else ""


def _grounded(value, request: str) -> bool:
    """True when this argument is something the user wrote: an address or name as written, a site by its name."""
    if isinstance(value, (list, tuple)):
        return bool(value) and all(_grounded(v, request) for v in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    if "@" not in text and ("://" in text or _DOMAIN.fullmatch(text)):
        return destination_key(text) in destinations_in(request)
    return text in request.lower()




@dataclass(frozen=True)
class Decision:
    allowed: bool
    asked: bool
    risk: int
    reason: str = ""
    approval_id: int | None = None  # the approval record behind a question that was asked (approvals.py)


@dataclass(frozen=True)
class Assessment:
    name: str
    args: dict
    risk: int
    decision: str  # allow | ask | deny
    question: str  # sanitised, ready to say to the user
    why: str = ""
    lifted: str = ""  # set when the user's own request answered the question (see Mandate)


def generic_prompt(name: str, args: dict) -> str:
    shown = ", ".join(f"{k} {v}" for k, v in (args or {}).items() if not _SECRET_ARG.search(str(k)))
    return f"Should I run {name.replace('_', ' ')}" + (f" with {shown}?" if shown else "?")


class Guard:
    """Decides, asks when needed, and keeps an audit trail of everything above class 0."""

    def __init__(self, store, tracer=None, audit_dir: Path | None = None, secret_values=lambda: [],
                 approvals=None):
        from minty_core.trace import Tracer

        self._approvals = approvals  # ApprovalStore: every question becomes a record bound to its action
        self.lockdown = None  # lockdown.Lockdown: while on, only the allowed local tools may run
        self.standing = None  # butler.standing.StandingApprovals: user-made, capped, expiring pre-approvals
        self._store = store
        self._tracer = tracer
        self._audit = Tracer(audit_dir, secret_values=secret_values, retention_days=366) if audit_dir else None

    def assess(self, tool, name: str, args: dict, prompt: str | None = None, *, extra_risk: int = 0, why: str = "",
               known: set[str] | None = None, request: str = "", user_origin: bool = True) -> Assessment:
        """What would happen to this call, without asking anyone. known: destinations the user has mentioned;
        data sent anywhere else is class 3 (personal data to a new party), so it is always asked."""
        cfg = self._store.config.safety
        args = args if isinstance(args, dict) else {}
        egress = getattr(tool, "egress", None)
        floor = R0
        if egress is not None and known is not None:
            try:
                dest = egress(args)
            except Exception:
                dest = "?"
            if dest and destination_key(dest) not in known:
                floor = R3
                why = why or f"sends data to {destination_key(dest)}, which you did not mention"
        risk = max(floor, min(R3, classify(tool, args) + extra_risk))
        lock = self.lockdown
        if lock is not None and lock.active:
            from minty_core.lockdown import allowed_during_lockdown
            if not allowed_during_lockdown(name):
                return Assessment(name, args, risk, "deny", safe_text(prompt or generic_prompt(name, args)),
                                  "lockdown is on")
        described = ""
        if prompt is None and getattr(tool, "describe", None) is not None:
            try:
                described = tool.describe(args) or ""
            except Exception:
                log.warning("describe() for %s failed", name, exc_info=True)
        question = safe_text(prompt or described or generic_prompt(name, args))
        decision = decide(name, risk, cfg.mode, cfg.rules, subject(name, args, question))
        if decision == "ask" and not cfg.confirm_risky and risk < R3:
            decision = "allow"  # "never ask" still asks for class 3
        lifted = ""
        if decision == "ask":
            lifted = self._mandated(tool, args, request, risk, extra_risk, user_origin)
            if lifted:
                decision = "allow"
        return Assessment(name, args, risk, decision, question, why, lifted)

    def _mandated(self, tool, args: dict, request: str, risk: int, extra_risk: int, user_origin: bool = True) -> str:
        """Why the user's own request already answers this question, or ''. Plain checks, so a persuasive page cannot argue with them."""
        mandate = getattr(tool, "mandate", None)
        if mandate is None or not request or extra_risk or risk > mandate.max_class or not getattr(self._store.config.safety, "mandates", True):
            return ""
        if risk > R2 and mandate.class3_flag and not (user_origin and getattr(self._store.config.safety, mandate.class3_flag, False)):
            return ""  # a class 3 lift needs its switch, and words the user typed (not a goal a model wrote for a mission)
        try:
            if mandate.intent and not re.search(mandate.intent, request, re.IGNORECASE):  # an empty intent leaves the whole decision to `check`
                return ""
        except re.error:
            return ""
        if any(not _grounded(args.get(name), request) for name in mandate.targets):
            return ""
        if mandate.check is not None:
            try:
                if not mandate.check(args, request):
                    return ""
            except Exception:  # noqa: BLE001 — a check that cannot be made is a question
                return ""
        return "you asked for this"

    async def authorize(self, tool, name: str, args: dict, confirm: Callable[[str], Awaitable[bool]] | None,
                        prompt: str | None = None, *, extra_risk: int = 0, why: str = "",
                        known: set[str] | None = None, request: str = "", user_origin: bool = True) -> Decision:
        a = self.assess(tool, name, args, prompt, extra_risk=extra_risk, why=why, known=known, request=request, user_origin=user_origin)
        if a.decision != "ask":
            return self.settle(a)
        covered = self._standing(a, extra_risk)
        if covered is not None:
            return covered
        record = self._open_approval(a)
        answer = False
        if confirm is not None:
            try:
                answer = bool(await confirm(a.question))
            except Exception:
                log.warning("confirmation failed; treating as no", exc_info=True)
        approval_id = None
        if record is not None:
            approval_id = record.id
            try:
                self._approvals.decide(record.id, answer, via="confirm")
                if answer and not self._approvals.consume(record.id, a.name, a.args):
                    answer = False  # fail closed: the record does not cover this action
            except Exception:  # noqa: BLE001 — bookkeeping failing leaves the user's own answer in charge
                log.warning("could not settle the approval record", exc_info=True)
        return self.settle(a, answer, approval_id)

    def _standing(self, a: Assessment, extra_risk: int) -> Decision | None:
        """A standing approval turns one specific, small question into a yes. It is checked only after every other
        check has already said "ask" (so class 3, new destinations, lockdown and tainted calls never get here
        as anything but a question) and it is refused again for class 3 and for a tainted call."""
        standing = self.standing
        cfg = getattr(getattr(self._store, "config", None), "butler", None)
        if standing is None or not getattr(cfg, "standing_approvals", False) or extra_risk or a.risk >= R3:
            return None
        try:
            grant = standing.match(a.name, a.args, a.risk)
            if grant is None:
                return None
            standing.use(grant)
        except Exception:  # noqa: BLE001 — if the store is unreadable the question is simply asked
            log.warning("standing approvals unavailable; asking instead", exc_info=True)
            return None
        if self._tracer is not None:
            self._tracer.event("standing_approval", tool=a.name, grant=grant.id, risk=a.risk)
        return self._record(a.name, a.args, a.risk,
                            Decision(True, False, a.risk, f"standing approval #{grant.id}"), a.why)

    def _open_approval(self, a: Assessment):
        if self._approvals is None or not getattr(self._store.config.safety, "approval_inbox", True):
            return None
        try:
            return self._approvals.request(a.name, a.args, a.question, a.risk)
        except Exception:  # noqa: BLE001
            log.warning("could not record the approval request", exc_info=True)
            return None

    def use_approval(self, approval_id: int, name: str, args: dict, risk: int = 0, target: str = "") -> Decision:
        """Run an action that was approved earlier (for example while a background job waited): allowed only when
        the approval covers exactly this action, has not expired and has not been used."""
        ok = False
        if self._approvals is not None:
            try:
                ok = self._approvals.consume(approval_id, name, args, target)
            except Exception:  # noqa: BLE001
                log.warning("could not check the approval record", exc_info=True)
        reason = "" if ok else "the approval does not match this action, has expired or was already used"
        return self._record(name, args, risk, Decision(ok, True, risk, reason, approval_id))

    def settle(self, a: Assessment, answer: bool | None = None, approval_id: int | None = None) -> Decision:
        """Record the outcome of an assessment; answer is the user's reply when it was asked."""
        if a.decision == "deny":
            return self._record(a.name, a.args, a.risk, Decision(False, False, a.risk, "blocked by a safety rule"),
                                a.why)
        if a.decision == "ask":
            if self._tracer is not None:
                self._tracer.event("confirm", tool=a.name, prompt=a.question, answer=bool(answer), risk=a.risk)
            return self._record(a.name, a.args, a.risk,
                                Decision(bool(answer), True, a.risk, "" if answer else "declined", approval_id),
                                a.why, a.question)
        return self._record(a.name, a.args, a.risk, Decision(True, False, a.risk, a.lifted), a.why)

    def _record(self, name, args, risk, decision: Decision, why: str = "", question: str = "") -> Decision:
        if self._audit is not None and self._store.config.safety.audit and (risk > R0 or not decision.allowed):
            self._audit.event("decision", tool=name, risk=RISK_NAMES[risk], allowed=decision.allowed,
                              asked=decision.asked, reason=decision.reason or why, question=question, args=args)
        return decision
