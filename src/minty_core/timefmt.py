"""One way to write a time of day, for the screen and for speech: 12-hour with AM and PM ("3:10 PM"), or 24-hour ("15:10") when the
`clock_24h` setting is on. The setting is applied when the configuration is loaded or saved."""
from datetime import datetime

_use_24h = False


def configure(clock_24h: bool) -> None:
    global _use_24h
    _use_24h = bool(clock_24h)


def is_24h() -> bool:
    return _use_24h


def _local(when: datetime | float) -> datetime:
    return when if isinstance(when, datetime) else datetime.fromtimestamp(when)


def clock12(when: datetime | float) -> str:
    """"9:30 AM", "12:00 PM", "11:59 PM", "12:05 AM": no leading zero on the hour. A timestamp is read in local time."""
    when = _local(when)
    hour = when.hour % 12 or 12
    return f"{hour}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


def clock24(when: datetime | float) -> str:
    when = _local(when)
    return f"{when.hour:02d}:{when.minute:02d}"


def clock_text(when: datetime | float) -> str:
    """The time as the setting says: 12-hour by default, 24-hour with `clock_24h`."""
    return clock24(when) if _use_24h else clock12(when)
