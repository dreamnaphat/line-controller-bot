"""Working-hours arithmetic for the worker (local time of the computer running the bot)."""

from datetime import datetime, time, timedelta
from typing import Sequence, Tuple


def parse_window(window: Sequence[str]) -> Tuple[time, time]:
    """["14:00", "16:30"] -> (time(14, 0), time(16, 30))."""
    start, end = (time.fromisoformat(part) for part in window)
    if end <= start:
        raise ValueError(f"ช่วงเวลาไม่ถูกต้อง: {window}")
    return start, end


def within(now: datetime, window: Tuple[time, time]) -> bool:
    return window[0] <= now.time() < window[1]


def start_of_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def next_window_start(now: datetime, window: Tuple[time, time]) -> datetime:
    """The next moment the window opens: later today if it has not opened yet, otherwise tomorrow."""
    today = datetime.combine(now.date(), window[0], tzinfo=now.tzinfo)
    return today if now < today else today + timedelta(days=1)
