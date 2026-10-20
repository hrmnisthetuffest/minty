"""Pull API keys out of a free-form notes file for ``minty-core import-keys``.

Only the documented provider labels and key prefixes below are recognised; anything else in the
file (prose, code samples, other services' tokens) is ignored.
"""

import re

_LABELS = {
    "groq": "groq",
    "openrouter": "openrouter",
    "mistral": "mistral",
    "cerebras": "cerebras",
    "gemini": "gemini",
    "googleaistudio": "gemini",
    "brave": "plugin.web_search.brave_key",
    "anthropic": "anthropic",
    "nvidia": "nvidia",
}

# "<label> [:|=] <value>" on a single line. The label must be separated from the value, so
# `nvidia_api_key = "..."` in a code sample is not read as a labelled line.
_LABELLED = re.compile(
    r"[ \t]*(groq|open[ \t]?router|mistral|cerebras|gemini|google[ \t]+ai[ \t]+studio|brave|anthropic|nvidia)"
    r"(?:[ \t]*[:=][ \t]*|[ \t]+)"
    r"[\"'`]?(?:bearer[ \t]+)?(\S{12,})[ \t]*",
    re.IGNORECASE,
)

# A prefix inside a longer token (e.g. "csk-" in the middle of another provider's key) is not a key.
_START = r"(?<![A-Za-z0-9_\-])"
_PATTERNS = {
    "nvidia": re.compile(_START + r"nvapi-[A-Za-z0-9_\-]{20,}"),
    "groq": re.compile(_START + r"gsk_[A-Za-z0-9]{20,}"),
    "openrouter": re.compile(_START + r"sk-or-v1-[A-Za-z0-9]{20,}"),
    "anthropic": re.compile(_START + r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "gemini": re.compile(_START + r"AIza[A-Za-z0-9_\-]{30,}"),
    "cerebras": re.compile(_START + r"csk-[A-Za-z0-9]{20,}"),
}

_QUOTES = "\"'`,;"
_MIN_LENGTH = 12
_URL = re.compile(r"[a-z][a-z0-9+.\-]*://", re.IGNORECASE)

# Mistral and Brave keys have no recognisable prefix, so a labelled value is only filtered for
# things that are clearly not a key: placeholders, paths and model names.
_NOT_A_KEY = re.compile(
    r"[/<>]|your|here|xxx|example|changeme|todo|not_"
    r"|-(?:latest|versatile|instruct)$"
    r"|^[a-z0-9.\-]+-\d+(?:\.\d+)?b?-"
    r"|\d+x\d+b",
    re.IGNORECASE,
)


def _clean(value: str) -> str | None:
    value = value.strip(_QUOTES)
    if len(value) < _MIN_LENGTH or _URL.match(value):
        return None
    return value


def _fits(name: str, value: str) -> bool:
    """Whether a labelled value plausibly is a key for ``name``."""
    if name in _PATTERNS:
        return _PATTERNS[name].fullmatch(value) is not None
    if any(pattern.search(value) for pattern in _PATTERNS.values()):
        return False  # carries another provider's prefix: the prefix pass will place it
    return _NOT_A_KEY.search(value) is None


def extract_keys(text: str) -> dict[str, str]:
    """Return ``{secret name: key}`` found in ``text``.

    Labelled lines win over unlabelled prefix matches, but a labelled value must look like a key
    for that provider (providers with a known prefix accept only that prefix; the rest reject
    placeholders and model names). For duplicates the first accepted labelled value is kept, else
    the first prefix match.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        m = _LABELLED.fullmatch(line)
        if m is None:
            continue
        name = _LABELS[re.sub(r"\s+", "", m.group(1).lower())]
        value = _clean(m.group(2))
        if value is not None and _fits(name, value):
            found.setdefault(name, value)
    for name, pattern in _PATTERNS.items():
        if name not in found and (m := pattern.search(text)):
            found[name] = m.group(0)
    return found
