"""Reading export files into the health hub: Hevy (CSV or API JSON), Cronometer daily-summary CSV, the Strava archive's
activities.csv and a generic Health Connect export (JSON or CSV).

The adapters follow the documented shapes of those exports; none has been run on a real file from your phone or apps
yet. A file that does not match is rejected with the reason and nothing from it is stored. The same file twice, or a
newer export that repeats old records, adds nothing twice.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from minty_core.health import HealthHub, parse_ts

log = logging.getLogger(__name__)

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
BAD_ROW_LIMIT = 0.2
LB = 0.45359237

ORIGIN_NAMES = {"hevy": "Hevy", "cronometer_csv": "Cronometer", "strava_export": "your Strava archive",
                "health_connect": "Health Connect"}
FORMAT_ORIGIN = {"hevy_csv": "hevy", "hevy_json": "hevy", "cronometer_csv": "cronometer_csv",
                 "strava_csv": "strava_export", "health_connect": "health_connect"}
DOMAIN_WORDS = {"training": "workouts", "body": "body measures", "sleep": "nights of sleep",
                "nutrition": "days of food", "activity": "activities"}


class ImportRejected(Exception):
    """The file is not something this can read; the message says why."""


@dataclass
class ImportReport:
    origin: str = ""
    rows: int = 0
    added: int = 0
    duplicates: int = 0
    ignored: int = 0
    problems: int = 0
    skipped_consent: dict[str, int] = field(default_factory=dict)
    already_imported: bool = False

    def text(self) -> str:
        name = ORIGIN_NAMES.get(self.origin, self.origin or "that file")
        if self.already_imported:
            return f"That file from {name} was already imported; nothing changed."
        parts = [f"Imported {self.added} new record{'s' * (self.added != 1)} from {name}"]
        if self.duplicates:
            parts.append(f"{self.duplicates} already there")
        if self.ignored:
            parts.append(f"{self.ignored} of a kind I don't keep")
        if self.problems:
            parts.append(f"{self.problems} unreadable row{'s' * (self.problems != 1)} skipped")
        out = ", ".join(parts) + "."
        if self.skipped_consent:
            what = ", ".join(f"{n} {DOMAIN_WORDS[d]}" for d, n in sorted(self.skipped_consent.items()))
            out += f" Not stored, because you have not agreed to those domains yet: {what}."
        return out


# ---- reading text ------------------------------------------------------------------------------------------
def _text(data: bytes) -> str | None:
    if b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _headers(text: str) -> list[str]:
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    try:
        return [h.strip().lower() for h in next(csv.reader([first]))] if first else []
    except (csv.Error, StopIteration):
        return []


def sniff(data: bytes, filename: str = "") -> str | None:
    """The format name, or None when this is not one of the exports above."""
    text = _text(data[:MAX_BYTES])
    if text is None or not text.strip():
        return None
    if text.lstrip()[0] in "{[":
        try:
            doc = json.loads(text)
        except ValueError:
            return None
        if isinstance(doc, dict) and isinstance(doc.get("workouts"), list):
            return "hevy_json"
        if isinstance(doc, dict) and isinstance(doc.get("records"), list):
            return "health_connect"
        if isinstance(doc, list) and doc and isinstance(doc[0], dict) and "type" in doc[0]:
            return "health_connect"
        return None
    head = set(_headers(text))
    if {"title", "start_time"} <= head:
        return "hevy_csv"
    if "date" in head and any(h.startswith("energy") for h in head):
        return "cronometer_csv"
    if {"activity id", "activity date"} <= head:
        return "strava_csv"
    if {"type", "start", "value"} <= head:
        return "health_connect"
    return None


_MARKERS = ("exercise_title", "exercise_template_id", "activity id", "energy (kcal)", "set_index")


def looks_like_health(data: bytes) -> bool:
    """A file that is not readable as an export but plainly was meant to be one (cut short, edited): it must not be
    handed to a model as an ordinary document."""
    text = _text(data[:200_000])
    if text is None:
        return False
    low = text.lower()
    return any(m in low for m in _MARKERS) or ('"records"' in low and '"type"' in low)


# ---- small parsers -----------------------------------------------------------------------------------------
def _when(value: str, utc: bool = False) -> tuple[float, bool]:
    value = str(value).strip()
    for fmt in ("%d %b %Y, %H:%M", "%b %d, %Y, %I:%M:%S %p", "%d %b %Y", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if utc:
            return naive.replace(tzinfo=timezone.utc).timestamp(), False
        return naive.astimezone().timestamp(), True
    return parse_ts(value)


def _num(value, default=None):
    try:
        text = str(value).strip().replace(",", "")
        return float(text) if text else default
    except ValueError:
        return default


def _digest(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _rows(text: str) -> list[dict]:
    rows = []
    for i, row in enumerate(csv.DictReader(io.StringIO(text))):
        if i >= MAX_ROWS:
            raise ImportRejected("that file has too many rows")
        rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k is not None})
    return rows


# ---- per format: -> (records, problems) ------------------------------------------------------------------
# a record: {"domain": ..., "key": ..., args...}
def _hevy_csv(text: str):
    head = set(_headers(text))
    missing = [c for c in ("exercise_title", "set_index") if c not in head]
    if missing:
        raise ImportRejected(f"that looks like a Hevy export but is missing: {', '.join(missing)}")
    groups: dict[tuple, dict] = {}
    problems = 0
    for row in _rows(text):
        try:
            start, assumed = _when(row["start_time"])
            end = _when(row["end_time"])[0] if row.get("end_time") else None
        except (ValueError, KeyError):
            problems += 1
            continue
        key = (row.get("title", ""), row["start_time"])
        w = groups.setdefault(key, {"domain": "training", "ext_id": _digest(*key), "started": start,
                                    "tz_assumed": assumed, "title": row.get("title", ""),
                                    "duration_s": int(end - start) if end and end > start else None, "sets": []})
        weight = _num(row.get("weight_kg"))
        if weight is None and row.get("weight_lbs"):
            weight = round(_num(row["weight_lbs"], 0) * LB, 2)
        w["sets"].append({"exercise": row.get("exercise_title", ""), "idx": int(_num(row.get("set_index"), 0)),
                          "kind": row.get("set_type") or "normal", "weight_kg": weight,
                          "reps": int(_num(row.get("reps"), 0)) if row.get("reps") else None,
                          "rpe": _num(row.get("rpe")),
                          "distance_m": (_num(row.get("distance_km"), 0) or 0) * 1000 or None,
                          "duration_s": _num(row.get("duration_seconds"))})
    return list(groups.values()), problems


def _hevy_json(text: str):
    doc = json.loads(text)
    out, problems = [], 0
    for w in doc.get("workouts", []):
        try:
            start, assumed = parse_ts(w["start_time"])
            end = parse_ts(w["end_time"])[0] if w.get("end_time") else None
            sets = [{"exercise": ex.get("title", ""), "idx": int(s.get("index", 0)), "kind": s.get("type", "normal"),
                     "weight_kg": s.get("weight_kg"), "reps": s.get("reps"), "rpe": s.get("rpe"),
                     "distance_m": s.get("distance_meters"), "duration_s": s.get("duration_seconds")}
                    for ex in w.get("exercises", []) for s in ex.get("sets", [])]
            out.append({"domain": "training", "ext_id": str(w["id"]), "started": start, "tz_assumed": assumed,
                        "title": w.get("title", ""), "duration_s": int(end - start) if end and end > start else None,
                        "sets": sets})
        except (KeyError, ValueError, TypeError):
            problems += 1
    return out, problems


def _cronometer(text: str):
    out, problems = [], 0
    for row in _rows(text):
        day = row.get("date", "")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            problems += 1
            continue
        pick = lambda prefix: next((_num(v) for k, v in row.items() if k.startswith(prefix)), None)  # noqa: E731
        out.append({"domain": "nutrition", "ext_id": day, "day": day, "kcal": pick("energy"),
                    "protein_g": pick("protein"), "carbs_g": pick("carbs"), "fat_g": pick("fat"),
                    "water_ml": pick("water")})
    return out, problems


def _strava(text: str):
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        raise ImportRejected("that file is empty")
    header = [h.strip().lower() for h in reader[0]]
    out, problems = [], 0

    def cols(name):  # every position of a (possibly repeated) column name
        return [i for i, h in enumerate(header) if h == name]

    for n, row in enumerate(reader[1:]):
        if n >= MAX_ROWS:
            raise ImportRejected("that file has too many rows")

        def cell(name, last=True):
            idx = cols(name)
            if not idx:
                return ""
            i = idx[-1] if last else idx[0]
            return row[i].strip() if i < len(row) else ""

        try:
            started, assumed = _when(cell("activity date"), utc=True)
            ident = cell("activity id") or _digest(cell("activity date"), cell("activity name"))
        except (ValueError, IndexError):
            problems += 1
            continue
        meters = _num(cell("distance", last=True))
        if meters is not None and len(cols("distance")) < 2:
            meters *= 1000  # a single Distance column in the archive is kilometres
        out.append({"domain": "activity", "ext_id": ident, "started": started, "tz_assumed": assumed,
                    "kind": cell("activity type") or "Activity", "distance_m": meters,
                    "duration_s": _num(cell("elapsed time", last=False)), "avg_hr": _num(cell("average heart rate")),
                    "elevation_m": _num(cell("elevation gain"))})
    return out, problems


def _health_connect(text: str):
    if text.lstrip()[0] in "{[":
        doc = json.loads(text)
        records = doc["records"] if isinstance(doc, dict) else doc
    else:
        records = _rows(text)
    out, problems, ignored = [], 0, 0
    for r in records:
        if not isinstance(r, dict):
            problems += 1
            continue
        kind = str(r.get("type", "")).lower()
        try:
            start, assumed = parse_ts(r["start"])
            ident = str(r.get("id") or _digest(kind, r["start"], r.get("value", "")))
            if kind in ("weight", "body_fat"):
                value, unit = float(r["value"]), str(r.get("unit") or ("%" if kind == "body_fat" else "kg"))
                if unit.lower() in ("lb", "lbs"):
                    value, unit = round(value * LB, 2), "kg"
                out.append({"domain": "body", "ext_id": ident, "ts": start, "tz_assumed": assumed, "kind": kind,
                            "value": value, "unit": unit})
            elif kind == "sleep":
                end = parse_ts(r["end"])[0]
                if end <= start:
                    raise ValueError("sleep ends before it starts")
                out.append({"domain": "sleep", "ext_id": ident, "start": start, "end": end, "tz_assumed": assumed,
                            "minutes": int((end - start) / 60), "quality": _num(r.get("quality"))})
            elif kind == "nutrition":
                day = datetime.fromtimestamp(start).strftime("%Y-%m-%d")
                out.append({"domain": "nutrition", "ext_id": ident, "day": day, "kcal": _num(r.get("kcal")),
                            "protein_g": _num(r.get("protein_g")), "carbs_g": _num(r.get("carbs_g")),
                            "fat_g": _num(r.get("fat_g")), "water_ml": _num(r.get("water_ml"))})
            elif kind == "exercise":
                end = parse_ts(r["end"])[0] if r.get("end") else None
                out.append({"domain": "activity", "ext_id": ident, "started": start, "tz_assumed": assumed,
                            "kind": str(r.get("kind") or "Exercise"), "distance_m": _num(r.get("distance_m")),
                            "duration_s": (end - start) if end and end > start else None,
                            "avg_hr": _num(r.get("avg_hr")), "elevation_m": _num(r.get("elevation_m"))})
            else:
                ignored += 1
        except (KeyError, ValueError, TypeError):
            problems += 1
    return out, problems, ignored


_PARSERS = {"hevy_csv": _hevy_csv, "hevy_json": _hevy_json, "cronometer_csv": _cronometer, "strava_csv": _strava}

_WRITERS = {
    "training": lambda h, o, r: h.add_workout(o, r["ext_id"], r["started"], r["title"], r["duration_s"], r["sets"],
                                              r["tz_assumed"]),
    "body": lambda h, o, r: h.add_body(o, r["ext_id"], r["ts"], r["kind"], r["value"], r["unit"], r["tz_assumed"]),
    "sleep": lambda h, o, r: h.add_sleep(o, r["ext_id"], r["start"], r["end"], r["minutes"], r["quality"],
                                         r["tz_assumed"]),
    "nutrition": lambda h, o, r: h.add_nutrition(o, r["ext_id"], r["day"], r["kcal"], r["protein_g"], r["carbs_g"],
                                                 r["fat_g"], r["water_ml"]),
    "activity": lambda h, o, r: h.add_activity(o, r["ext_id"], r["started"], r["kind"], r["distance_m"],
                                               r["duration_s"], r["avg_hr"], r["elevation_m"], r["tz_assumed"]),
}


def store_records(hub: HealthHub, origin: str, records: list[dict]) -> ImportReport:
    """Write parsed records, skipping (and counting) any domain you have not agreed to."""
    report = ImportReport(origin=origin, rows=len(records))
    for record in records:
        domain = record["domain"]
        if not hub.consent(domain):
            report.skipped_consent[domain] = report.skipped_consent.get(domain, 0) + 1
            continue
        if _WRITERS[domain](hub, origin, record):
            report.added += 1
        else:
            report.duplicates += 1
    return report


def import_bytes(hub: HealthHub, data: bytes, filename: str = "", kind: str | None = None) -> ImportReport:
    if len(data) > MAX_BYTES:
        raise ImportRejected("that file is too big (20 MB at most)")
    text = _text(data)
    if text is None:
        raise ImportRejected("that doesn't look like a health export (it isn't text)")
    kind = kind or sniff(data, filename)
    if kind is None:
        if text.lstrip()[:1] in ("{", "["):
            try:
                json.loads(text)
            except ValueError:
                raise ImportRejected("that file is not valid JSON (it may have been cut short)") from None
        raise ImportRejected("that doesn't look like a Hevy, Cronometer, Strava or Health Connect export")
    origin = FORMAT_ORIGIN[kind]
    digest = hashlib.sha256(data).hexdigest()
    if hub.seen_file(digest):
        return ImportReport(origin=origin, already_imported=True)
    try:
        if kind == "health_connect":
            records, problems, ignored = _health_connect(text)
        else:
            records, problems = _PARSERS[kind](text)
            ignored = 0
    except (ValueError, KeyError) as e:
        raise ImportRejected(f"that file is not valid ({type(e).__name__})") from None
    good = len(records)
    if problems and (good == 0 or problems / (problems + good) > BAD_ROW_LIMIT):
        raise ImportRejected(f"{problems} of {problems + good} rows were unreadable, so I stored nothing")
    if not records and not ignored:
        raise ImportRejected("that file has no records in it")
    report = store_records(hub, origin, records)
    report.rows, report.ignored, report.problems = good + problems + ignored, ignored, problems
    if not report.skipped_consent:  # a file held back for consent can be imported again once you agree
        hub.log_import(digest, origin, report.rows, report.added)
    return report
