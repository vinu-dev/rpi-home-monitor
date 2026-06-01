# REQ: SWR-072; RISK: RISK-001, RISK-015; SEC: SC-001, SC-002, SC-008; TEST: TC-057
"""Restart-schedule normalisation and next-run helpers.

The persisted shape is intentionally small so both server and camera can
round-trip it over the existing heartbeat/control channel:

    {"enabled": false, "days": ["sun"], "time": "03:30",
     "updated_at": "2026-06-01T01:02:03Z", "source": "server"}
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_SET = set(DAY_ORDER)
DEFAULT_RESTART_TIME = "03:30"
DEFAULT_RESTART_DAYS = ("sun",)
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_time(value: object) -> str:
    if not isinstance(value, str) or not TIME_RE.match(value):
        return DEFAULT_RESTART_TIME
    hour_s, minute_s = value.split(":", 1)
    try:
        hour = int(hour_s)
        minute = int(minute_s)
    except ValueError:
        return DEFAULT_RESTART_TIME
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return DEFAULT_RESTART_TIME


def _valid_days(value: object) -> list[str]:
    if isinstance(value, str):
        raw_days = [part.strip().lower() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_days = [str(part).strip().lower() for part in value]
    else:
        raw_days = []
    days = sorted({day for day in raw_days if day in DAY_SET}, key=DAY_ORDER.index)
    return days or list(DEFAULT_RESTART_DAYS)


def normalise_restart_schedule(
    payload: object,
    *,
    source: str = "server",
    stamp: bool = False,
    now_iso: str | None = None,
) -> dict:
    """Return a safe restart-schedule dict from arbitrary input."""
    raw = payload if isinstance(payload, dict) else {}
    src = str(raw.get("source") or source or "server").strip().lower()
    if src not in {"server", "camera", "system"}:
        src = source if source in {"server", "camera", "system"} else "system"

    updated_at = str(raw.get("updated_at") or "").strip()
    if stamp:
        updated_at = now_iso or utc_now_iso()

    return {
        "enabled": bool(raw.get("enabled", False)),
        "days": _valid_days(raw.get("days")),
        "time": _valid_time(raw.get("time")),
        "updated_at": updated_at,
        "source": src,
    }


def schedule_updated_at(schedule: object) -> str:
    if not isinstance(schedule, dict):
        return ""
    return str(schedule.get("updated_at") or "")


def is_schedule_newer(candidate: object, current: object) -> bool:
    return schedule_updated_at(candidate) > schedule_updated_at(current)


def timezone_or_utc(timezone: str | None):
    try:
        return ZoneInfo(timezone or "UTC")
    except ZoneInfoNotFoundError:
        return UTC


def next_run_at(schedule: object, timezone: str | None, *, now=None) -> str:
    """Return the next local scheduled reboot as an ISO timestamp, or empty."""
    sched = normalise_restart_schedule(schedule, stamp=False)
    if not sched["enabled"]:
        return ""
    tz = timezone_or_utc(timezone)
    now_dt = now.astimezone(tz) if now is not None else datetime.now(tz)
    hour, minute = [int(part) for part in sched["time"].split(":", 1)]
    wanted_days = set(sched["days"])
    for offset in range(8):
        candidate_day = now_dt.date() + timedelta(days=offset)
        candidate_dt = datetime(
            candidate_day.year,
            candidate_day.month,
            candidate_day.day,
            hour,
            minute,
            tzinfo=tz,
        )
        if DAY_ORDER[candidate_dt.weekday()] not in wanted_days:
            continue
        if candidate_dt <= now_dt:
            continue
        return candidate_dt.replace(second=0, microsecond=0).isoformat()
    return ""


def scheduled_minute_key(now_dt: datetime, schedule: object) -> str:
    """Return a stable key when ``now_dt`` is inside the scheduled minute."""
    sched = normalise_restart_schedule(schedule, stamp=False)
    if not sched["enabled"]:
        return ""
    if DAY_ORDER[now_dt.weekday()] not in set(sched["days"]):
        return ""
    if now_dt.strftime("%H:%M") != sched["time"]:
        return ""
    return now_dt.strftime("%Y-%m-%dT%H:%M")
