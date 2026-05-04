# REQ: SWR-033, SWR-041; RISK: RISK-016; SEC: SC-015; TEST: TC-031
"""Quiet-hours schedule helpers for notification delivery."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from monitor.services.time_window import DAY_INDEX, match_schedule_window, parse_hhmm

VALID_DAYS = frozenset(DAY_INDEX)
_TZ_SEARCH_ROOTS = (
    os.environ.get("TZDIR", ""),
    "/usr/share/zoneinfo",
    "/usr/lib/zoneinfo",
    "/usr/share/lib/zoneinfo",
    "C:\\msys64\\usr\\share\\zoneinfo",
    "C:\\Program Files\\Git\\usr\\share\\zoneinfo",
    "C:\\cygwin64\\usr\\share\\zoneinfo",
)


@dataclass(frozen=True)
class QuietHoursDecision:
    """Resolved quiet-hours decision for one event."""

    quiet: bool
    source: str
    window_key: str = ""


def _load_timezone(key: str):
    if not key or key.upper() in {"UTC", "ETC/UTC", "Z"}:
        return UTC

    try:
        return ZoneInfo(key)
    except Exception:
        pass

    relative = Path(*str(key).split("/"))
    for root in _TZ_SEARCH_ROOTS:
        if not root:
            continue
        candidate = Path(root) / relative
        if not candidate.is_file():
            continue
        try:
            with candidate.open("rb") as handle:
                return ZoneInfo.from_file(handle, key=key)
        except Exception:
            continue

    return UTC


def evaluate_quiet_hours(
    *,
    now: datetime,
    user_schedule: list[dict],
    camera_override: list[dict] | None,
    tz: str,
) -> QuietHoursDecision:
    """Return whether active delivery should be suppressed.

    ``camera_override`` semantics:
      ``None`` -> inherit ``user_schedule``
      ``[]``   -> explicit "always loud"
      list     -> use the camera-specific schedule instead of the user default
    """
    source = "camera" if camera_override is not None else "user"
    schedule = list(camera_override if camera_override is not None else user_schedule)
    if not schedule:
        return QuietHoursDecision(quiet=False, source=source)

    zone = _load_timezone(tz)

    ref = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    local_now = ref.astimezone(zone)
    match = match_schedule_window(schedule, local_now)
    if match is None:
        return QuietHoursDecision(quiet=False, source=source)

    return QuietHoursDecision(
        quiet=True,
        source=source,
        window_key=f"{source}:{match.index}:{match.start_date.isoformat()}",
    )


def validate_schedule(schedule: Any, label: str = "notification_schedule") -> str:
    """Validate the persisted quiet-hours schedule shape."""
    if not isinstance(schedule, list):
        return f"{label} must be a list"

    for idx, item in enumerate(schedule):
        item_label = f"{label}[{idx}]"
        if not isinstance(item, dict):
            return f"{item_label} must be an object"
        if set(item.keys()) != {"days", "start", "end"}:
            return f"{item_label} must have exactly keys 'days', 'start', 'end'"

        days = item["days"]
        if not isinstance(days, list) or not days:
            return f"{item_label}.days must be a non-empty list"
        for day in days:
            if day not in VALID_DAYS:
                return f"{item_label}.days has invalid day: {day!r}"

        start = parse_hhmm(item["start"])
        end = parse_hhmm(item["end"])
        if start is None:
            return f"{item_label}.start must match HH:MM"
        if end is None:
            return f"{item_label}.end must match HH:MM"
        if start == end:
            return f"{item_label} must not use the same start and end time"

    return ""
