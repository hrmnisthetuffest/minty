"""Translation with your glossary (664). The translating is done by the model; this keeps the list of terms that must come out a
certain way (names, school words, Minty itself), tells the model which of them the text contains, and checks the result afterwards
for any it missed."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from minty_core.create.db import CreateDB

LANGS = {"hindi": "hi", "punjabi": "pa", "french": "fr", "spanish": "es", "german": "de", "english": "en", "urdu": "ur",
         "tamil": "ta", "bengali": "bn", "gujarati": "gu", "marathi": "mr", "japanese": "ja", "chinese": "zh",
         "arabic": "ar", "russian": "ru", "portuguese": "pt", "italian": "it", "korean": "ko"}
NAMES = {v: k.capitalize() for k, v in LANGS.items()}


def language(text: str) -> str:
    t = " ".join(str(text).lower().split())
    if t in LANGS:
        return LANGS[t]
    if t in NAMES.values() or t in LANGS.values():
        return t
    if re.fullmatch(r"[a-z]{2,3}", t):
        return t
    raise ValueError("Name a language such as Hindi, Punjabi or French (or its two-letter code).")


@dataclass
class Prep:
    language: str
    constraints: list[tuple[str, str]] = field(default_factory=list)
    text: str = ""


def _present(term: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I) is not None


class TranslationGlossary:
    def __init__(self, db: CreateDB):
        self.db = db

    def add(self, term: str, lang: str, translation: str) -> None:
        term, translation, code = " ".join(str(term).split()), " ".join(str(translation).split()), language(lang)
        if not term or not translation or len(term) > 60 or len(translation) > 120:
            raise ValueError("A glossary entry needs a term (up to 60 characters) and its translation (up to 120).")
        with self.db.lock:
            self.db.db.execute("INSERT INTO glossary(term, lang, translation) VALUES (?,?,?) "
                               "ON CONFLICT(term, lang) DO UPDATE SET translation=excluded.translation",
                               (term, code, translation))
            self.db.db.commit()

    def remove(self, term: str, lang: str) -> bool:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM glossary WHERE term=? AND lang=?", (" ".join(term.split()), language(lang))).rowcount
            self.db.db.commit()
        return bool(n)

    def terms(self, lang: str | None = None) -> list[tuple[str, str]]:
        with self.db.lock:
            if lang:
                rows = self.db.db.execute("SELECT term, translation FROM glossary WHERE lang=? ORDER BY term", (language(lang),))
            else:
                rows = self.db.db.execute("SELECT term, translation FROM glossary ORDER BY lang, term")
            return [(r["term"], r["translation"]) for r in rows.fetchall()]

    def prepare(self, text: str, lang: str) -> Prep:
        code = language(lang)
        found = [(t, tr) for t, tr in self.terms(code) if _present(t, text)]
        name = NAMES.get(code, code)
        out = f"Translate the text into {name}. Keep names, numbers and web addresses unchanged."
        if found:
            out += " Use exactly these translations: " + "; ".join(f'"{t}" → "{tr}"' for t, tr in found) + "."
        return Prep(code, found, out + " Reply with the translation only.")

    def check(self, source: str, translated: str, lang: str) -> list[str]:
        return [f"'{t}' should be '{tr}'" for t, tr in self.prepare(source, lang).constraints
                if tr.casefold() not in str(translated).casefold()]
