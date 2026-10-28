"""The background-video catalogue: which style of footage is shown today, and which clip within it.

Every clip in the asset library can be filed under a category (nature, city, space, your own Minecraft parkour ...). A style is chosen
for the day, not for every video, so a day looks consistent; the next day gets a different one (never the same two days running
while there is a choice, and every style comes round before one repeats). Within a style, a clip that was not just shown is picked,
favourites more often. Exclusions, favourites and weights are obeyed. A style with very few clips is skipped for one with enough.
Everything is kept in the library's own database, so a restart changes nothing.

No language model is involved: the choice is plain arithmetic on what was shown before.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from minty_core.studio.assets import Asset, AssetLibrary


class CatalogError(Exception):
    pass


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    queries: dict  # provider -> search phrases; empty: only footage you bring yourself (nothing may be fetched for it)


CATEGORIES: dict[str, Category] = {c.key: c for c in (
    Category("parkour", "Minecraft parkour (your own recordings)", {}),
    Category("gameplay", "Other gameplay (your own recordings)", {}),
    Category("nature", "Nature: forests, rain, oceans",
             {"pexels": ("forest rain nature", "ocean waves", "waterfall river", "mountain mist"),
              "pixabay": ("nature forest", "ocean waves", "rain window")}),
    Category("city", "City and travel",
             {"pexels": ("city night traffic", "street neon night", "train window travel", "rooftop skyline"),
              "pixabay": ("city night", "traffic timelapse", "train journey")}),
    Category("satisfying", "Satisfying processes and crafts",
             {"pexels": ("satisfying process", "woodworking craft", "pottery clay", "paint pouring"),
              "pixabay": ("satisfying", "craft hands", "pottery")}),
    Category("abstract", "Abstract loops and animation",
             {"pexels": ("abstract loop", "colorful liquid", "particles animation", "neon lights abstract"),
              "pixabay": ("abstract loop", "particles", "liquid color")}),
    Category("space", "Space and science (NASA)",
             {"nasa": ("nebula", "aurora", "earth from space", "galaxy", "moon surface")}),
)}
STATES = ("ready", "missing", "bad", "evicted")
DEFAULT_PREFS = {"mode": "daily", "exclude_categories": [], "exclude_assets": [], "favourites": [], "weights": {}}
RETRY_AFTER_ERROR_S = 6 * 3600
RECENT_WINDOW = 8  # the last few clips of a style are not shown again while others are left


@dataclass(frozen=True)
class Clip:
    asset: Asset
    category: str
    quality: float
    provider: str
    external_id: str
    state: str
    plays: int
    last_played: float


@dataclass(frozen=True)
class Choice:
    asset: Asset
    category: str
    reason: str


def _day(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _ordinal(day: str) -> int:
    return datetime.strptime(day, "%Y-%m-%d").toordinal()


class Catalog:
    def __init__(self, lib: AssetLibrary, *, min_clips: int = 3):
        self.lib, self.min_clips = lib, max(1, int(min_clips))
        self._db, self._lock = lib.shared()
        with self._lock:
            self._db.executescript(
                "CREATE TABLE IF NOT EXISTS clip_meta (asset_id INTEGER PRIMARY KEY, category TEXT NOT NULL, quality REAL NOT NULL DEFAULT 1,"
                " provider TEXT NOT NULL DEFAULT '', external_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'ready');"
                "CREATE INDEX IF NOT EXISTS clip_meta_cat ON clip_meta(category, state);"
                "CREATE TABLE IF NOT EXISTS plays (id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id INTEGER NOT NULL,"
                " category TEXT NOT NULL, at REAL NOT NULL, day TEXT NOT NULL);"
                "CREATE INDEX IF NOT EXISTS plays_day ON plays(day);"
                "CREATE INDEX IF NOT EXISTS plays_asset ON plays(asset_id);"
                "CREATE TABLE IF NOT EXISTS catalog_prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS discover_log (id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, category TEXT NOT NULL,"
                " at REAL NOT NULL, day TEXT NOT NULL, found INTEGER NOT NULL, kept INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '');"
                "CREATE INDEX IF NOT EXISTS discover_log_pc ON discover_log(provider, category, id);")
            self._db.commit()

    # ---- preferences --------------------------------------------------------------------------------------------
    def prefs(self) -> dict:
        with self._lock:
            rows = dict(self._db.execute("SELECT key, value FROM catalog_prefs").fetchall())
        out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_PREFS.items()}
        for k, raw in rows.items():
            if k in out:
                try:
                    out[k] = json.loads(raw)
                except ValueError:
                    pass
        if "salt" in rows:
            out["salt"] = rows["salt"]
        else:  # a private starting point, so two people's rotations are not in step
            out["salt"] = hashlib.sha256(os.urandom(8)).hexdigest()[:8]
            with self._lock:
                self._db.execute("INSERT OR IGNORE INTO catalog_prefs(key, value) VALUES ('salt', ?)", (out["salt"],))
                self._db.commit()
        return out

    def set_pref(self, key: str, value) -> None:
        if key == "mode":
            if value not in ("daily", "each_video"):
                raise CatalogError("mode is daily (one style a day) or each_video")
        elif key == "exclude_categories":
            bad = [c for c in value if c not in CATEGORIES]
            if not isinstance(value, list) or bad:
                raise CatalogError(f"unknown category {bad or value!r}; the categories are: {', '.join(CATEGORIES)}")
        elif key in ("exclude_assets", "favourites"):
            if not isinstance(value, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in value):
                raise CatalogError(f"{key} is a list of clip numbers")
        elif key == "weights":
            if not isinstance(value, dict) or any(c not in CATEGORIES or not isinstance(w, (int, float)) or isinstance(w, bool) or w < 0
                                                  for c, w in value.items()):
                raise CatalogError("weights is a number of 0 or more for each category (0 switches a style off)")
        else:
            raise CatalogError(f"unknown preference {key!r}")
        with self._lock:
            self._db.execute("INSERT INTO catalog_prefs(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, json.dumps(value)))
            self._db.commit()

    # ---- the clips ----------------------------------------------------------------------------------------------
    def tag(self, asset: Asset, category: str, *, provider: str | None = None, external_id: str | None = None,
            quality: float | None = None) -> None:
        """File a clip under a style. What is not given is kept if the clip was filed before."""
        if category not in CATEGORIES:
            raise CatalogError(f"unknown category {category!r}; the categories are: {', '.join(CATEGORIES)}")
        with self._lock:
            old = self._db.execute("SELECT provider, external_id, quality FROM clip_meta WHERE asset_id=?", (asset.id,)).fetchone() or ("", "", 1.0)
            self._db.execute(
                "INSERT INTO clip_meta(asset_id, category, quality, provider, external_id) VALUES (?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET"
                " category=excluded.category, quality=excluded.quality, provider=excluded.provider, external_id=excluded.external_id,"
                " state='ready'", (asset.id, category, max(0.05, float(old[2] if quality is None else quality)),
                                   old[0] if provider is None else provider, old[1] if external_id is None else external_id))
            self._db.commit()

    def known(self, provider: str, external_id: str) -> bool:
        """Fetched from this provider before (in any state): a clip that was dropped or went bad is not fetched again."""
        with self._lock:
            return self._db.execute("SELECT 1 FROM clip_meta WHERE provider=? AND external_id=?", (provider, external_id)).fetchone() is not None

    def mark(self, asset_id: int, state: str) -> None:
        if state not in STATES:
            raise CatalogError(f"state must be one of: {', '.join(STATES)}")
        with self._lock:
            self._db.execute("UPDATE clip_meta SET state=? WHERE asset_id=?", (state, int(asset_id)))
            self._db.commit()

    def clips(self, category: str | None = None, states: tuple = ("ready",)) -> list[Clip]:
        with self._lock:
            rows = self._db.execute(
                "SELECT a.id, a.path, a.kind, a.license, a.source, a.attribution, a.note, a.sha, a.added, a.parents, m.category, m.quality,"
                " m.provider, m.external_id, m.state, (SELECT COUNT(*) FROM plays p WHERE p.asset_id=a.id),"
                " COALESCE((SELECT MAX(at) FROM plays p WHERE p.asset_id=a.id), 0) FROM clip_meta m JOIN assets a ON a.id=m.asset_id"
                " WHERE (? IS NULL OR m.category=?) ORDER BY a.id", (category, category)).fetchall()
        return [Clip(self.lib._asset(r[:10]), r[10], r[11], r[12], r[13], r[14], r[15], r[16]) for r in rows if r[14] in states]

    def _ready(self) -> dict[str, list[Clip]]:
        """Ready clips by category. A file that has gone from the disk is marked missing here and left out."""
        out: dict[str, list[Clip]] = {}
        for clip in self.clips():
            if not os.path.isfile(clip.asset.path):
                self.mark(clip.asset.id, "missing")
                continue
            out.setdefault(clip.category, []).append(clip)
        return out

    def counts(self) -> dict[str, int]:
        return {c: len(v) for c, v in self._ready().items() if v}

    def summary(self) -> str:
        prefs, counts = self.prefs(), self.counts()
        lines = []
        for key, c in CATEGORIES.items():
            off = key in prefs["exclude_categories"] or prefs["weights"].get(key) == 0
            note = " (off)" if off else (" (only footage you add)" if not c.queries and not counts.get(key) else "")
            lines.append(f"{key}: {counts.get(key, 0)}{note} - {c.label}")
        lines.append(f"Mode: {prefs['mode']}; favourites: {len(prefs['favourites'])}; clips switched off: {len(prefs['exclude_assets'])}.")
        return "\n".join(lines)

    # ---- choosing -----------------------------------------------------------------------------------------------
    def _eligible(self, prefs: dict, only: str | None = None) -> dict[str, list[Clip]]:
        weights, gone = prefs["weights"], set(prefs["exclude_assets"])
        out = {}
        for category, clips in self._ready().items():
            if only is not None:  # asked for by name: a style switched off for the rotation can still be asked for
                if category != only:
                    continue
            elif category in prefs["exclude_categories"] or weights.get(category) == 0:
                continue
            kept = [c for c in clips if c.asset.id not in gone]
            if kept:
                out[category] = kept
        return out

    def _last_play_on(self, day: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT category FROM plays WHERE day=? ORDER BY id DESC LIMIT 1", (day,)).fetchone()
        return row[0] if row else None

    def _last_play_before(self, day: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT category FROM plays WHERE day<? ORDER BY id DESC LIMIT 1", (day,)).fetchone()
        return row[0] if row else None

    def _last_play_ever(self) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT category FROM plays ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def _last_day_shown(self) -> dict[str, str]:
        with self._lock:
            return dict(self._db.execute("SELECT category, MAX(day) FROM plays GROUP BY category").fetchall())

    def _style_for(self, today: str, eligible: dict, prefs: dict) -> tuple[str, str]:
        daily = prefs["mode"] == "daily"
        if daily and (todays := self._last_play_on(today)) in eligible:
            return todays, "today's style"
        previous = self._last_play_before(today) if daily else self._last_play_ever()
        shown, salt, weights = self._last_day_shown(), prefs["salt"], prefs["weights"]

        def rank(category: str):
            age = _ordinal(today) - _ordinal(shown[category]) if category in shown else 10 ** 6  # never shown: first in line
            tie = int(hashlib.sha256(f"{salt}:{today}:{category}".encode()).hexdigest()[:8], 16)
            return (age * float(weights.get(category, 1.0)), tie)

        pool = [c for c in eligible if c != previous] or list(eligible)
        pick = max(pool, key=rank)
        n = len(eligible[pick])
        if n >= self.min_clips:
            return pick, "its turn" if previous else "first style"
        roomy = [c for c in eligible if len(eligible[c]) >= self.min_clips]
        note = f"{CATEGORIES[pick].key} has only {n} clip{'s' * (n != 1)}"
        if roomy:
            alt = max([c for c in roomy if c != previous] or roomy, key=rank)
            return alt, f"{note}, so {alt} instead"
        return pick, f"{note}, but so does every style: add or fetch more footage for more variety"  # take turns anyway

    def _clip_in(self, clips: list[Clip], category: str, prefs: dict, rng: random.Random) -> Clip:
        window = min(len(clips) - 1, RECENT_WINDOW)
        recent: set[int] = set()
        if window > 0:
            with self._lock:
                rows = self._db.execute("SELECT asset_id FROM plays WHERE category=? ORDER BY id DESC LIMIT ?", (category, window)).fetchall()
            recent = {r[0] for r in rows}
        pool = [c for c in clips if c.asset.id not in recent] or clips
        favourites, now = set(prefs["favourites"]), time.time()

        def weight(c: Clip) -> float:
            idle = 1.0 if not c.last_played else min(3.0, 1.0 + max(0.0, now - c.last_played) / (7 * 86400))
            return c.quality * (3.0 if c.asset.id in favourites else 1.0) * idle

        return rng.choices(pool, weights=[weight(c) for c in pool])[0]

    def _record(self, clip: Clip, category: str, now: datetime) -> None:
        with self._lock:
            self._db.execute("INSERT INTO plays(asset_id, category, at, day) VALUES (?,?,?,?)",
                             (clip.asset.id, category, now.timestamp(), _day(now)))
            self._db.commit()

    def choose(self, *, rng: random.Random | None = None, now: datetime | None = None, record: bool = True,
               only: str | None = None) -> Choice | None:
        """The background for a video made now: None when there is nothing to show (then `fallback` makes a plain one). `only`
        names a style to take it from instead of today's."""
        rng, now = rng or random.Random(), now or datetime.now()
        prefs = self.prefs()
        eligible = self._eligible(prefs, only)
        if not eligible:
            return None
        category, reason = (only, f"{only}, as asked") if only else self._style_for(_day(now), eligible, prefs)
        clip = self._clip_in(eligible[category], category, prefs, rng)
        if record:
            self._record(clip, category, now)
        return Choice(clip.asset, category, reason)

    def shuffle(self, *, rng: random.Random | None = None, now: datetime | None = None) -> Choice | None:
        """Something different now: another style for the rest of the day (or another clip when there is only one style)."""
        rng, now = rng or random.Random(), now or datetime.now()
        prefs = self.prefs()
        eligible = self._eligible(prefs)
        if not eligible:
            return None
        today = _day(now)
        current = self._last_play_on(today)
        current = current if current in eligible else self._style_for(today, eligible, prefs)[0]
        others = [c for c in eligible if c != current]
        category, why = (rng.choices(others, weights=[float(prefs["weights"].get(c, 1.0)) for c in others])[0], "shuffled") if others \
            else (current, "shuffled: only one style, so another clip")
        clip = self._clip_in(eligible[category], category, prefs, rng)
        self._record(clip, category, now)
        return Choice(clip.asset, category, why)

    # ---- looking for more: how often ---------------------------------------------------------------------------------
    def log_run(self, provider: str, category: str, now: datetime, found: int, kept: int, error: str = "") -> None:
        with self._lock:
            self._db.execute("INSERT INTO discover_log(provider, category, at, day, found, kept, error) VALUES (?,?,?,?,?,?,?)",
                             (provider, category, now.timestamp(), _day(now), found, kept, error[:200]))
            self._db.commit()

    def runs(self, provider: str, category: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM discover_log WHERE provider=? AND category=? AND error=''", (provider, category)).fetchone()[0]

    def due(self, provider: str, category: str, now: datetime) -> tuple[bool, str]:
        """May this provider be asked about this style now? Once a day; after a failure, not for a few hours."""
        with self._lock:
            last = self._db.execute("SELECT at, day, error FROM discover_log WHERE provider=? AND category=? ORDER BY id DESC LIMIT 1",
                                    (provider, category)).fetchone()
        if last is None:
            return True, ""
        at, day, error = last
        if error:
            return (False, f"the last try at {provider} failed ({error[:80]}); waiting a few hours") if now.timestamp() - at < RETRY_AFTER_ERROR_S else (True, "")
        return (False, f"{provider} was already searched for {category} today") if day == _day(now) else (True, "")

    # ---- keeping the library inside its limit ---------------------------------------------------------------------
    def prune(self, max_bytes: int) -> list[int]:
        """Delete the least recently shown fetched clips (never your own, never a favourite) until the fetched ones fit. The record
        and its licence stay: videos already made from a clip still need them."""
        favourites = set(self.prefs()["favourites"])
        fetched = [c for c in self.clips() if c.provider not in ("", "own") and os.path.isfile(c.asset.path)]
        total = sum(os.path.getsize(c.asset.path) for c in fetched)
        gone = []
        for clip in sorted(fetched, key=lambda c: (c.last_played, c.asset.id)):
            if total <= max_bytes:
                break
            if clip.asset.id in favourites:
                continue
            size = os.path.getsize(clip.asset.path)
            try:
                os.unlink(clip.asset.path)
            except OSError:
                continue
            self.mark(clip.asset.id, "evicted")
            total -= size
            gone.append(clip.asset.id)
        return gone

    # ---- when there is nothing else -----------------------------------------------------------------------------------
    async def fallback(self, work_dir: Path, seconds: float = 20.0) -> Asset:
        """A plain animated colour background made here (no third party), kept so it is made once."""
        from minty_core.studio import media

        external = f"gradient-{int(seconds)}"
        for clip in self.clips("abstract"):
            if clip.provider == "generated" and clip.external_id == external and os.path.isfile(clip.asset.path):
                return clip.asset
        out = Path(work_dir) / f"background-{external}.mp4"
        await media.generated_background(out, seconds)
        asset = self.lib.add(out, "video", "generated", source="a plain animated colour background made here", note=external)
        self.tag(asset, "abstract", provider="generated", external_id=external, quality=0.4)
        return asset
