"""Keep secrets out of log lines and log files.

uvicorn logs the address of every request, query string included. A key or token that travels in a query string would therefore
land in the console, a systemd journal and Minty's log file. Secrets now travel in headers, and this filter hides whatever
secret-looking query value still turns up (a script, an old client). The log file itself is private to its owner.
"""
from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler

# ?token=... &api_key=... &access_token=... &password=... : the value, up to the next & or space or quote
_SECRET_QUERY = re.compile(r"(?i)([?&](?:[a-z0-9_.-]*(?:token|key|secret|password|passwd|auth|signature)|pw|pwd|sig|code)=)[^&\s\"'#]+")
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def redact_query_secrets(text: str) -> str:
    return _SECRET_QUERY.sub(r"\1***", text)


class QuerySecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # a malformed log call is not ours to fix
            return True
        clean = redact_query_secrets(text)
        if clean != text:
            record.msg, record.args = clean, ()
        return True


def install_uvicorn_redaction() -> list[tuple[logging.Logger, logging.Filter]]:
    """Put the filter on the loggers uvicorn writes request lines to. Returns what was added (tests remove it again)."""
    added = []
    for name in UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(f, QuerySecretFilter) for f in lg.filters):
            flt = QuerySecretFilter()
            lg.addFilter(flt)
            added.append((lg, flt))
    return added


class PrivateRotatingFileHandler(RotatingFileHandler):
    """A rotating log file only its owner can read: mode 0600 from the first byte, an older looser file tightened, and the same
    for every file a rollover starts."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)
        except BaseException:
            os.close(fd)
            raise
