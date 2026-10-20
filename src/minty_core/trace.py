"""Append-only JSONL traces of what Minty did: turns, model calls, tool calls, confirmations, jobs.

One file per day under <config>/traces/. Secrets never reach disk: fields whose name looks like a credential
are masked, and any stored secret value appearing anywhere is replaced. Long strings are cut.
"""
import contextlib
import contextvars
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# "tokens" (plural) names a count such as tokens_in, and "..._ms" a duration such as first_token_ms: neither is a credential;
# a "token" field (access_token, api_token, token) holds one. (first_token_ms used to be masked, so no first-token latency was
# ever recorded.)
SECRET_FIELD = re.compile(r"key|token(?!s|_ms)|secret|password|passwd|authorization|cookie", re.IGNORECASE)
MAX_STR = 300
MAX_ITEMS = 20
LONG_LISTS = {"tool_names": 80}  # lists that are only useful whole (which tools a model call was offered)
RETENTION_DAYS = 14
_SECRETS_TTL_S = 30.0

# The turn being handled: {"turn_id": ..., plus fields annotate_turn() adds}. Tasks started inside a turn inherit it.
_TURN: contextvars.ContextVar[dict | None] = contextvars.ContextVar("minty_turn", default=None)


@contextlib.contextmanager
def turn_scope():
    """Every trace event and history row inside carries one turn id. A nested scope keeps the outer turn."""
    current = _TURN.get()
    if current is not None:
        yield current
        return
    info = {"turn_id": uuid.uuid4().hex[:12]}
    token = _TURN.set(info)
    try:
        yield info
    finally:
        _TURN.reset(token)


def current_turn_id() -> str | None:
    info = _TURN.get()
    return info["turn_id"] if info else None


def turn_fields() -> dict:
    """What has been noted about the current turn (toolkits offered, transcript hash), without the id."""
    info = _TURN.get() or {}
    return {k: v for k, v in info.items() if k != "turn_id"}


def annotate_turn(**fields) -> None:
    info = _TURN.get()
    if info is not None:
        info.update(fields)


def transcript_hash(text: str) -> str:
    """Joins a turn to its words without storing them: the same request (case/space aside) gives the same hash."""
    norm = " ".join((text or "").lower().split())
    return hashlib.sha256(norm.encode()).hexdigest()[:12] if norm else ""


class NullTracer:
    """Same API, records nothing. The default wherever tracing is optional."""

    def event(self, kind: str, **data) -> None:
        return None

    @contextlib.contextmanager
    def span(self, kind: str, **data):
        yield data

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class Tracer(NullTracer):
    def __init__(self, directory: Path, secret_values: Callable[[], list[str]] = lambda: [],
                 clock: Callable[[], float] = time.time, retention_days: int = RETENTION_DAYS,
                 redactor: Callable[[str], str] | None = None, static: dict | None = None):
        self._redactor = redactor  # applied to every string written (personal data masking)
        self._static = dict(static or {})  # fields on every record, e.g. {"machine": "laptop"}
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self._secret_values = secret_values
        self._clock = clock
        self._lock = threading.Lock()
        self._file = None
        self._day = ""
        self._secrets: list[str] = []
        self._secrets_at = -_SECRETS_TTL_S
        self._prune(retention_days)

    # ---- writing -------------------------------------------------------------------------------
    def event(self, kind: str, **data) -> None:
        now = self._clock()
        record = {"ts": round(now, 3), "kind": kind, **self._static}
        turn_id = current_turn_id()
        if turn_id and "turn_id" not in data:
            record["turn_id"] = turn_id
        record.update(self._clean(data))
        try:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            line = json.dumps({"ts": record["ts"], "kind": kind, "error": "unserialisable trace data"})
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        with self._lock:
            try:
                if day != self._day or self._file is None:
                    if self._file is not None:
                        self._file.close()
                    path = self.directory / f"{day}.jsonl"
                    self._file = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered: survives crashes
                    path.chmod(0o600)
                    self._day = day
                self._file.write(line + "\n")
            except OSError as e:
                log.warning("could not write trace: %s", e)

    @contextlib.contextmanager
    def span(self, kind: str, **data):
        """Times the block; the caller may add fields to the yielded dict. Errors are recorded and re-raised."""
        start = time.perf_counter()
        try:
            yield data
        except BaseException as e:
            data.setdefault("outcome", "cancelled" if type(e).__name__ == "CancelledError" else "error")
            data.setdefault("error", type(e).__name__)
            raise
        finally:
            data["ms"] = round((time.perf_counter() - start) * 1000, 1)
            self.event(kind, **data)

    def flush(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.flush()

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    # ---- privacy ---------------------------------------------------------------------------------
    def _secret_list(self) -> list[str]:
        now = time.monotonic()
        if now - self._secrets_at > _SECRETS_TTL_S:
            try:
                self._secrets = sorted((v for v in self._secret_values() if v and len(v) >= 8), key=len, reverse=True)
            except Exception:
                log.warning("could not read secrets for trace redaction", exc_info=True)
            self._secrets_at = now
        return self._secrets

    def _clean(self, value, depth: int = 0, max_items: int = MAX_ITEMS):
        if isinstance(value, dict):
            out = {}
            for k, v in list(value.items())[:MAX_ITEMS * 2]:
                key = str(k)
                out[key] = "…" if SECRET_FIELD.search(key) and v not in (None, "", 0) \
                    else self._clean(v, depth + 1, LONG_LISTS.get(key, MAX_ITEMS))
            return out
        if isinstance(value, (list, tuple, set)):
            items = [self._clean(v, depth + 1) for v in list(value)[:max_items]]
            if len(value) > max_items:
                items.append(f"(+{len(value) - max_items} more)")
            return items
        if isinstance(value, str):
            for secret in self._secret_list():
                if secret in value:
                    value = value.replace(secret, "[secret]")
            if self._redactor is not None:
                try:
                    value = self._redactor(value)
                except Exception:  # noqa: BLE001 — masking must never lose an event
                    log.warning("trace redactor failed", exc_info=True)
            return value if len(value) <= MAX_STR else value[:MAX_STR] + "…"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._clean(str(value), depth + 1)

    # ---- housekeeping ----------------------------------------------------------------------------
    def _prune(self, days: int) -> None:
        cutoff = time.strftime("%Y-%m-%d", time.localtime(self._clock() - days * 86400))
        for path in self.directory.glob("*.jsonl"):
            if path.stem < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass


def read_events(directory: Path, day: str | None = None) -> list[dict]:
    """All events of one day (default: today), skipping damaged lines."""
    day = day or time.strftime("%Y-%m-%d")
    path = Path(directory) / f"{day}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
