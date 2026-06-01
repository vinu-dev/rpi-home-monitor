# REQ: SWR-072; RISK: RISK-001, RISK-015; SEC: SC-001, SC-002, SC-008; TEST: TC-057
"""Restart-schedule normalisation and next-run helpers for camera runtime."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

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
    source: str = "camera",
    stamp: bool = False,
    now_iso: str | None = None,
) -> dict:
    raw = payload if isinstance(payload, dict) else {}
    src = str(raw.get("source") or source or "camera").strip().lower()
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


def next_run_at(schedule: object, *, now=None) -> str:
    sched = normalise_restart_schedule(schedule, stamp=False)
    if not sched["enabled"]:
        return ""
    now_dt = now.astimezone() if now is not None else datetime.now().astimezone()
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
            tzinfo=now_dt.tzinfo,
        )
        if DAY_ORDER[candidate_dt.weekday()] not in wanted_days:
            continue
        if candidate_dt <= now_dt:
            continue
        return candidate_dt.replace(second=0, microsecond=0).isoformat()
    return ""


def scheduled_minute_key(now_dt: datetime, schedule: object) -> str:
    sched = normalise_restart_schedule(schedule, stamp=False)
    if not sched["enabled"]:
        return ""
    if DAY_ORDER[now_dt.weekday()] not in set(sched["days"]):
        return ""
    if now_dt.strftime("%H:%M") != sched["time"]:
        return ""
    return now_dt.strftime("%Y-%m-%dT%H:%M")
