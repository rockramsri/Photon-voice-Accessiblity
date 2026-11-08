"""Spoken pickup day/time -> a concrete New York datetime.

Handles what callers actually say: "today", "tomorrow", "friday",
"after four", "nine thirty in the morning", "noon", "this evening".
Returns None when it can't be sure, so the agent asks again.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "thirty": 30, "forty five": 45, "forty-five": 45,
}
_PARTS = {"morning": 9, "noon": 12, "midday": 12, "afternoon": 14, "evening": 18, "tonight": 18}


def parse_day(day: str, now: datetime) -> datetime | None:
    text = day.lower()
    if any(w in text for w in ("today", "tonight", "this evening", "this afternoon", "this morning")):
        return now
    if "tomorrow" in text:
        return now + timedelta(days=1)
    for i, name in enumerate(_DAYS):
        if name in text:
            ahead = (i - now.weekday()) % 7
            if ahead == 0 and "next" in text:
                ahead = 7
            return now + timedelta(days=ahead)
    return None


def parse_time(time: str) -> tuple[int, int] | None:
    text = time.lower().replace(".", "")
    for word, value in sorted(_NUMS.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"\b{word}\b", str(value), text)

    m = re.search(r"(?<!\d)(\d{1,2})(?:[:\s](\d{2}))?(?!\d)", text)
    if not m:
        for part, hour in _PARTS.items():
            if part in text:
                return hour, 0
        return None

    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    if hour <= 12:
        pm = re.search(r"(?<![a-z])pm\b|afternoon|evening|tonight|night", text)
        am = re.search(r"(?<![a-z])am\b|morning", text)
        if pm and hour < 12:
            hour += 12
        elif am and hour == 12:
            hour = 0
        elif not am and not pm and 1 <= hour <= 7:
            hour += 12  # "after four" at a pharmacy means 4 pm
    return hour, minute


def parse_pickup(day: str, time: str, now: datetime | None = None) -> datetime | None:
    """Combined parse; None if either half is unclear or the time already passed."""
    now = now or datetime.now(TZ)
    base = parse_day(day, now)
    hm = parse_time(time)
    if base is None or hm is None:
        return None
    when = base.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    return when if when > now else None
