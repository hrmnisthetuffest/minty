"""Run the tests with the clock moved forward, to find tests that only pass on the day they were written.

  MINTY_TEST_SHIFT_DAYS=60 pytest -q

A test that fixes a date in its data and then uses the real clock fails when the clock is moved. `datetime.now()`, `date.today()`,
`time.time()`, `time.localtime()`, `time.gmtime()` and `time.strftime()` without a time move; `time.monotonic()` (durations) and file times do not, so a handful of failures about file ages are expected noise."""
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
    seconds = shift.total_seconds()
    real_localtime, real_gmtime, real_strftime = _time.localtime, _time.gmtime, _time.strftime
    _time.time = lambda: real_time() + seconds
    _time.localtime = lambda secs=None: real_localtime(real_time() + seconds if secs is None else secs)
    _time.gmtime = lambda secs=None: real_gmtime(real_time() + seconds if secs is None else secs)
    _time.strftime = lambda fmt, tup=None: real_strftime(fmt, _time.localtime() if tup is None else tup)
    return True
