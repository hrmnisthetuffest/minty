"""Run the tests with the clock moved forward, to find tests that only pass on the day they were written.

  MINTY_TEST_SHIFT_DAYS=60 pytest -q

A test that fixes a date in its data and then uses the real clock fails when the clock is moved. `datetime.now()`, `date.today()` and
`time.time()` move; `time.monotonic()` (durations) and file times do not, so a handful of failures about file ages are expected noise."""
import datetime as _datetime
import os
import time as _time


def install() -> bool:
    days = float(os.environ.get("MINTY_TEST_SHIFT_DAYS", "0") or 0)
    if not days:
        return False
    shift = _datetime.timedelta(days=days)
    real_time = _time.time
    real_datetime, real_date = _datetime.datetime, _datetime.date

    class ShiftedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.fromtimestamp(real_time(), tz) + shift

        @classmethod
        def utcnow(cls):
            return real_datetime.fromtimestamp(real_time(), _datetime.timezone.utc).replace(tzinfo=None) + shift

        @classmethod
        def today(cls):
            return real_datetime.fromtimestamp(real_time()) + shift

    class ShiftedDate(real_date):
        @classmethod
        def today(cls):
            return real_date.fromtimestamp(real_time()) + shift

    _datetime.datetime = ShiftedDatetime
    _datetime.date = ShiftedDate
    _time.time = lambda: real_time() + shift.total_seconds()
    return True
