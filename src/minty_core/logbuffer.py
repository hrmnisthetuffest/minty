import collections
import logging
from urllib.parse import quote, quote_plus
from typing import Callable


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = 500, secret_values: Callable[[], list[str]] | None = None):
        super().__init__()
        self._records: collections.deque = collections.deque(maxlen=capacity)
        self._secret_values = secret_values or (lambda: [])
        self._seen: set[str] = set()

    def _remember(self) -> None:
        for value in self._secret_values():
            if value and len(value) >= 8:
                # request URLs carry keys percent-encoded; redact those forms as well
                self._seen.update((value, quote(value, safe=""), quote_plus(value)))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                message += f" | {type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
            self._remember()
            row = {"time": record.created, "level": record.levelname, "levelno": record.levelno,
                   "name": record.name, "message": message}
            self.acquire()
            try:
                self._records.append(row)
            finally:
                self.release()
        except Exception:
            self.handleError(record)

    def _redact(self, text: str) -> str:
        self._remember()
        for value in sorted(self._seen, key=len, reverse=True):
            text = text.replace(value, "***")
        return text

    def recent(self, limit: int = 200, min_level: str = "INFO", name: str = "") -> list[dict]:
        floor = logging.getLevelName(min_level.upper())
        floor = floor if isinstance(floor, int) else logging.INFO
        self.acquire()
        try:
            snapshot = list(self._records)
        finally:
            self.release()
        rows = [r for r in snapshot if r["levelno"] >= floor and (not name or r["name"] == name or r["name"].startswith(name + "."))][-limit:]
        return [
            {"time": r["time"], "level": r["level"], "name": r["name"], "message": self._redact(r["message"])}
            for r in rows
        ]
