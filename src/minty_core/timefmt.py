"""One way to write a time of day: 12-hour with AM and PM ("3:10 PM"), for the screen and for speech."""
from datetime import datetime


def clock12(when: datetime | float) -> str:
    """"9:30 AM", "12:00 PM", "11:59 PM", "12:05 AM": no leading zero on the hour. A timestamp is read in local time."""
    if not isinstance(when, datetime):
        when = datetime.fromtimestamp(when)
    hour = when.hour % 12 or 12
    return f"{hour}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"
