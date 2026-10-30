"""Where an argument came from: untrusted content must not quietly become the target of an action.

Results of tools that read the outside world (web pages, mail, search, files, APIs) are untrusted data. While a user
request is being served, the addresses, links, hosts and paths that appear in that data are remembered. If a tool
that changes something is then called with one of them, and the user never wrote it themselves, the call is asked
about (class +1) with the source named. Content that also tries to give orders ("ignore your instructions", "send
this to ...") marks the rest of the turn: every change then asks. It never allows anything; it only asks more.
"""
from __future__ import annotations

import re

UNTRUSTED_PREFIXES = ("shell_", "browser_", "web_search_", "mail_", "research_", "apis_", "files_read", "n8n_", "school_",
                      "inbox_", "telegram_", "mcp_", "spotify_search", "history_", "web_open", "web_later", "web_captions",
                      "system_notifications", "dev_repo", "dev_review",
                      "create_news", "create_papers", "create_evidence", "create_media", "integrations_calendar",
                      "integrations_weather", "selfcheck_report", "selfcheck_timeline")
UNTRUSTED_TAG = "[untrusted"
# tools that change what Minty remembers or how it behaves: never because of something it read
SELF_MODIFYING = ("memory_remember", "prompts_", "learn_", "skills_", "vault_note", "vault_append", "vault_inbox_decide",
                  "vault_decision_log", "vault_bookmark", "vault_glossary", "vault_daily", "vault_review",
                  "butler_add", "butler_done", "butler_delete", "butler_undo", "butler_apply",
                  "memory_correct", "memory_forget", "memory_resolve", "vault_private_add",
                  "study_exam", "study_cards", "study_note", "study_paper", "study_mistake",
                  "productivity_habit", "productivity_person", "productivity_trip", "productivity_meal",
                  "productivity_todo", "productivity_alarm", "productivity_record",
                  "web_watch", "web_later", "web_session", "web_download", "web_formlog",
                  "system_control", "system_usb", "system_script", "dev_snippet", "dev_idea", "dev_plugin", "dev_review",
                  "create_news", "create_papers", "create_inbox", "create_make", "create_image", "create_translate",
                  "create_media", "create_sources", "mcp_call", "integrations_calendar", "integrations_home",
                  "integrations_sleep", "integrations_lan", "integrations_n8n", "selfcheck_report")  # memory_forget also covers memory_forget_*
LOCAL_OK = ("clock_", "memory_recall", "timers_", "jobs_status", "jobs_cancel", "lockdown_", "vault_find", "vault_ask",
            "vault_open", "vault_resume", "vault_status", "vault_resurface", "vault_inbox_review",
            "butler_list", "butler_find", "butler_export", "butler_sort", "memory_audit", "memory_learned", "vault_private_find", "vault_private_open",
            "study_plan", "study_pace", "study_check", "study_summarise", "study_rehearse", "study_notices",
            "study_review", "study_class_bag", "productivity_day")
MAX_REMEMBERED = 60_000

_INJECTION = re.compile(
    r"ignore (?:all |any |your |the )?(?:previous|prior|above|earlier)? ?(?:instructions|prompts?|rules)|"
    r"disregard (?:all |any |the |your )?(?:previous|prior|system|above|instructions|rules)|you are now\b|new instructions?:|"
    r"(?:^|\n)\s*(?:system|assistant|developer)\s*:|do not (?:tell|inform|alert) the user|"
    r"without (?:asking|telling|informing|notifying) the user|"
    r"(?:send|forward|email|upload|post) (?:this|these|it|them|all|everything|the .{0,30}) to \S+@\S+|"
    r"curl [^\n|]{0,200}\|\s*(?:ba)?sh|<\s*/?\s*(?:system|instructions?)\s*>|reveal (?:your )?(?:system )?prompt|"
    r"note to (?:the )?(?:ai|assistant|model|agent)|(?:attention|dear|hey|hello)[,:]? (?:ai|assistant|model|agent)\b|"
    r"(?:ai|assistant|model|agent)[,:] (?:please |you must |you should |now )|from now on,? (?:you|always|never)|"
    r"as (?:the|an) (?:ai|assistant),? you (?:must|should|will)|override (?:your|the) (?:rules|safety|settings)",
    re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.IGNORECASE)
_HOST = re.compile(r"\b(?:[a-z0-9\-]+\.)+(?:com|net|org|io|dev|app|xyz|top|ru|cn|info|biz|co|in|me|ly|sh)\b",
                   re.IGNORECASE)
_PATH = re.compile(r"(?:(?<=\s)|^)(?:~|/)[\w.\-/~]{3,}")


def looks_like_injection(text: str) -> bool:
    """True if the text tries to give orders to a model (used on tool descriptions from outside)."""
    return bool(_INJECTION.search(str(text or "")))


def is_untrusted_source(tool_name: str, result: str = "") -> bool:
    return tool_name.startswith(UNTRUSTED_PREFIXES) or str(result)[:60].startswith(UNTRUSTED_TAG)


class Provenance:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        """A new user request: forget what the last one read."""
        self._seen = ""
        self.flagged = False  # untrusted content in this request tried to give orders
        self.sources: list[str] = []

    def note_result(self, tool_name: str, result: str) -> None:
        if not is_untrusted_source(tool_name, result):
            return
        text = str(result or "")
        if len(self._seen) < MAX_REMEMBERED:
            self._seen += "\n" + text[:MAX_REMEMBERED - len(self._seen)]
        if tool_name not in self.sources:
            self.sources.append(tool_name)
        if _INJECTION.search(text):
            self.flagged = True

    # ---- the question asked before a call --------------------------------------------------------
    def tainted(self, args, user_text: str) -> list[str]:
        """Values in `args` that appear in untrusted content this request read and that the user never wrote."""
        if not self._seen:
            return []
        seen, theirs = self._seen.lower(), (user_text or "").lower()
        out: list[str] = []
        for value in _strings(args):
            for candidate in _candidates(value):
                c = candidate.lower().rstrip(".,;:)")
                if len(c) >= 6 and c in seen and c not in theirs and candidate not in out:
                    out.append(candidate)
        return out

    def note_for(self, args, user_text: str, tool_name: str = "") -> str:
        """The sentence added to a confirmation, or ''."""
        if tool_name.startswith(SELF_MODIFYING) and self.sources:
            return (f"Note: I read {self._source()} while handling this, and I only change myself or save facts "
                    "because you said so")
        taint = self.tainted(args, user_text)
        if taint:
            shown = ", ".join(taint[:2])
            return f"Note: {shown} came from {self._source()}, not from you"
        if self.flagged:
            return f"Note: {self._source()} contained instructions addressed to me, which I ignore"
        return ""

    def extra_risk(self, args, user_text: str, tool_name: str = "") -> int:
        """Added to the tool's class. A request whose content tried to give orders asks before anything that is not
        purely local (+2: a read becomes a question); an address the user never wrote, or a self-change after
        reading, is +1."""
        if self.flagged and not tool_name.startswith(LOCAL_OK):
            return 2
        if tool_name.startswith(SELF_MODIFYING) and self.sources:
            return 1
        return 1 if self.tainted(args, user_text) else 0

    def _source(self) -> str:
        kinds = {"browser": "a web page", "web_search": "search results", "mail": "an e-mail", "files": "a file",
                 "research": "web research", "apis": "an API reply", "n8n": "a workflow reply"}
        for name in self.sources:
            prefix = name.split("_", 1)[0]
            if prefix in kinds:
                return kinds[prefix]
        return "content I read"


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _candidates(value: str) -> list[str]:
    out = [m.group(0) for rx in (_EMAIL, _URL, _HOST, _PATH) for m in rx.finditer(value)]
    if len(value) >= 6 and len(value) <= 200 and "\n" not in value:
        out.append(value.strip())
    return out
