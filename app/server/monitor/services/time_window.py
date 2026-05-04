"""Shared weekday/HH:MM schedule helpers for server-side policies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime

DAY_INDEX = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


@dataclass(frozen=True)
class ScheduleMatch:
    """A schedule window that matches a concrete local datetime."""

    index: int
    start_date: date
    overnight: bool
    window: dict


def parse_hhmm(value: str) -> dtime | None:
    """Parse ``HH:MM`` into ``datetime.time``. Return None on invalid input."""
    if not isinstance(value, str) or not _TIME_RE.match(value):
        return None
    try:
        hh, mm = value.split(":")
        return dtime(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def match_schedule_window(schedule: list[dict], now: datetime) -> ScheduleMatch | None:
    """Return the first schedule window that contains ``now``.

    Each window shape is ``{"days": [...], "start": "HH:MM", "end": "HH:MM"}``.
    Overnight windows (``end <= start``) spill into the next day.
    Malformed entries are skipped so callers fail open.
    """
    if not schedule:
        return None

    today_idx = now.weekday()
    yesterday_idx = (today_idx - 1) % 7
    current = now.time()
    today = now.date()
    yesterday = today - timedelta(days=1)

    for idx, item in enumerate(schedule):
        if not isinstance(item, dict):
            continue
        days = item.get("days") or []
        start = parse_hhmm(item.get("start", ""))
        end = parse_hhmm(item.get("end", ""))
        if not isinstance(days, list) or start is None or end is None:
            continue

        day_keys = {d for d in days if d in DAY_INDEX}
        if not day_keys:
            continue

        today_match = any(DAY_INDEX[d] == today_idx for d in day_keys)
        yest_match = any(DAY_INDEX[d] == yesterday_idx for d in day_keys)

        if end > start:
            if today_match and start <= current < end:
                return ScheduleMatch(
                    index=idx,
                    start_date=today,
                    overnight=False,
                    window=item,
                )
            continue

        if today_match and current >= start:
            return ScheduleMatch(
                index=idx,
                start_date=today,
                overnight=True,
                window=item,
            )
        if yest_match and current < end:
            return ScheduleMatch(
                index=idx,
                start_date=yesterday,
                overnight=True,
                window=item,
            )

    return None


def now_in_window(schedule: list[dict], now: datetime) -> bool:
    """Return True when ``now`` falls inside any schedule window."""
    return match_schedule_window(schedule, now) is not None
