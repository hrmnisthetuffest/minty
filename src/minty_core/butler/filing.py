"""Photo-to-notes filing (1057): a captioned photo or PDF becomes a note in sources/ (the text, not the picture).

It is outside content, so the note is untrusted; ID numbers in it are masked. No model is involved."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from minty_core.policy import safe_text
from minty_core.secretscan import mask_pii

_TRIGGER = re.compile(r"^(?:please )?(?:file|save|keep|store|doc(?:ument)?)\b[\s:,\-]*(?P<rest>.*)$", re.I)
_KINDS = {"receipt": r"receipt|payment received|paid", "bill": r"\bbill\b|invoice|statement|amount due",
          "certificate": r"certificate|certify", "ticket": r"ticket|boarding|admit card",
          "notice": r"notice|circular", "letter": r"\bdear\b|sincerely|regards"}


@dataclass
class FilingPlan:
    title: str
    body: str
    tags: list[str]
    masked: bool


def wants_filing(caption: str) -> bool:
    return bool(_TRIGGER.match(str(caption or "").strip()))


def plan_filing(text: str, caption: str, now: datetime) -> FilingPlan:
    m = _TRIGGER.match(str(caption or "").strip())
    rest = " ".join((m.group("rest") if m else "").split())
    while True:  # "file this photo: Hall ticket" -> "Hall ticket"
        trimmed = re.sub(r"^(?:this|it|that|photo|picture|pic|document|doc|please)\b[\s:,\-]*", "", rest,
                         flags=re.I)
        if trimmed == rest:
            break
        rest = trimmed
    first = next((l.strip() for l in str(text or "").splitlines() if l.strip()), "")
    title = safe_text(rest or first or f"Photo {now:%Y-%m-%d}", 60)
    body = mask_pii(str(text or "")).strip()
    low = str(text or "").lower()
    tags = ["filed-photo", *[k for k, rx in _KINDS.items() if re.search(rx, low)]]
    return FilingPlan(title, body + ("\n" if body else ""), tags, body != str(text or "").strip())


async def file_photo(vault_provider, cfg, kind: str, name: str, text: str, caption: str, forwarded: bool,
                     now: datetime | None = None) -> str | None:
    """A reply when the photo was filed, else None (the normal path goes on)."""
    if not (cfg.butler.enabled and cfg.butler.photo_filing and vault_provider.active()
            and kind in ("image", "pdf", "text") and wants_filing(caption)):
        return None
    plan = plan_filing(text, caption, now or datetime.now())
    try:
        note = vault_provider.kit().vault.create(plan.title, plan.body, folder="sources", origin="file",
                                                 source=f"telegram {kind}: {safe_text(name, 40)}", tags=plan.tags)
    except Exception:  # noqa: BLE001
        return None
    extra = " ID numbers in it are masked." if plan.masked else ""
    return f'Filed "{plan.title}" in your notes (note:{note.id}). I kept the text, not the picture.{extra}'
