"""Where the request being handled came from: "laptop" (voice, the terminal, the panel) or "telegram".

A question goes back to the place it was asked: a question raised while handling a Telegram message is offered on Telegram and nowhere else, and one
raised on the laptop never is. The origin follows the work (it is a context variable, copied into the tasks it starts) and a job keeps it, so a
mission started from the phone still asks on the phone after a restart.
"""
import contextlib
import contextvars

LAPTOP, TELEGRAM = "laptop", "telegram"
_origin: contextvars.ContextVar[str] = contextvars.ContextVar("minty_origin", default=LAPTOP)


def current_origin() -> str:
    return _origin.get()


def set_origin(name: str) -> None:
    """For a task that owns its context (a background job): from here on this is where its requests came from."""
    _origin.set(name if name in (LAPTOP, TELEGRAM) else LAPTOP)


@contextlib.contextmanager
def origin_scope(name: str):
    token = _origin.set(name if name in (LAPTOP, TELEGRAM) else LAPTOP)
    try:
        yield
    finally:
        _origin.reset(token)
