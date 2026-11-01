"""The few standard-library features Minty uses that only exist from Python 3.11, so that Python 3.10 (the oldest supported version) works too."""
import asyncio


class _Timeout:
    """`asyncio.timeout` for Python 3.10: after `delay` seconds the task waiting inside is cancelled and that cancellation becomes the built-in TimeoutError.
    `expired()` says whether it was this timeout that fired (as on 3.11). A cancellation from anywhere else stays a cancellation."""

    def __init__(self, delay: float | None):
        self._delay = delay
        self._task: asyncio.Task | None = None
        self._handle: asyncio.TimerHandle | None = None
        self._expired = False

    def expired(self) -> bool:
        return self._expired

    def _expire(self) -> None:
        self._expired = True
        self._task.cancel()

    async def __aenter__(self) -> "_Timeout":
        if self._delay is not None:
            self._task = asyncio.current_task()
            self._handle = asyncio.get_running_loop().call_later(max(self._delay, 0), self._expire)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            self._handle.cancel()
        if self._expired and exc_type is asyncio.CancelledError:
            raise TimeoutError from exc


timeout = getattr(asyncio, "timeout", _Timeout)
