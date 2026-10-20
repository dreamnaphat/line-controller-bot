from datetime import datetime, time, timedelta, timezone

import pytest

from bot.schedule import next_window_start, parse_window, start_of_day, within

BKK = timezone(timedelta(hours=7))
WINDOW = parse_window(["14:00", "16:30"])


def at(hour, minute=0, day=20):
    return datetime(2026, 10, day, hour, minute, tzinfo=BKK)


def test_parse_window():
    assert WINDOW == (time(14, 0), time(16, 30))
    with pytest.raises(ValueError):
        parse_window(["16:30", "14:00"])


@pytest.mark.parametrize("now, expected", [
    (at(13, 59), False), (at(14, 0), True), (at(16, 29), True), (at(16, 30), False),
])
def test_within(now, expected):
    assert within(now, WINDOW) is expected


def test_next_window_start():
    assert next_window_start(at(9), WINDOW) == at(14)
    assert next_window_start(at(15), WINDOW) == at(14, day=21)
    assert next_window_start(at(17), WINDOW) == at(14, day=21)


def test_start_of_day():
    assert start_of_day(at(15, 42)) == at(0)
