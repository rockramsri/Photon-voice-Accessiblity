from datetime import datetime

import pytest

from voice.spoken_time import TZ, parse_pickup, parse_time

NOW = datetime(2026, 11, 7, 10, 0, tzinfo=TZ)  # Saturday 10am


@pytest.mark.parametrize("text,expected", [
    ("after four", (16, 0)), ("4 pm", (16, 0)), ("nine thirty in the morning", (9, 30)),
    ("noon", (12, 0)), ("this evening", (18, 0)), ("10am", (10, 0)), ("5:45", (17, 45)),
])
def test_parse_time(text, expected):
    assert parse_time(text) == expected


def test_tomorrow_after_four():
    when = parse_pickup("tomorrow", "after four", NOW)
    assert (when.day, when.hour) == (8, 16)


def test_weekday_rolls_forward():
    when = parse_pickup("monday", "noon", NOW)
    assert when.strftime("%A") == "Monday" and when.day == 9


def test_past_or_unclear_returns_none():
    assert parse_pickup("today", "nine in the morning", NOW) is None
    assert parse_pickup("someday", "four", NOW) is None
    assert parse_pickup("tomorrow", "whenever", NOW) is None
