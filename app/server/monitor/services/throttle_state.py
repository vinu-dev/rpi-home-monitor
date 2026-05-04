# REQ: SWR-004, SWR-017, SWR-032; RISK: RISK-005, RISK-015; SEC: SC-002, SC-008, SC-020; TEST: TC-005, TC-012, TC-014, TC-029
"""Helpers for Raspberry Pi throttle-state payloads.

The camera heartbeat reports a Pi-specific ``throttle_state`` object with
current + sticky bit-flags decoded from ``vcgencmd get_throttled``. These
helpers keep the server-side ingestion, summary, and alert surfaces aligned
on one interpretation of that payload.
"""

from __future__ import annotations

from typing import Any

THROTTLE_BOOL_FIELDS = (
    "under_voltage_now",
    "under_voltage_sticky",
    "frequency_capped_now",
    "frequency_capped_sticky",
    "throttled_now",
    "throttled_sticky",
    "soft_temp_limit_now",
    "soft_temp_limit_sticky",
)

_CURRENT_LABELS = (
    ("under_voltage_now", "Under-voltage"),
    ("throttled_now", "Thermal throttle"),
    ("frequency_capped_now", "Frequency capped"),
    ("soft_temp_limit_now", "Soft temp limit"),
)

_STICKY_LABELS = (
    ("under_voltage_sticky", "Under-voltage"),
    ("throttled_sticky", "Thermal throttle"),
    ("frequency_capped_sticky", "Frequency capped"),
    ("soft_temp_limit_sticky", "Soft temp limit"),
)


def sanitize_throttle_state(raw: Any) -> dict | None:
    """Return a bounded throttle-state dict or None for invalid input."""
    if not isinstance(raw, dict):
        return None

    cleaned: dict[str, Any] = {}
    saw_flag = False
    for field in THROTTLE_BOOL_FIELDS:
        if field in raw:
            cleaned[field] = bool(raw[field])
            saw_flag = True
        else:
            cleaned[field] = False

    last_updated = raw.get("last_updated")
    source = raw.get("source")
    raw_value_hex = raw.get("raw_value_hex")

    if isinstance(last_updated, str):
        cleaned["last_updated"] = last_updated[:32]
    elif saw_flag:
        cleaned["last_updated"] = ""

    if isinstance(source, str):
        cleaned["source"] = source[:32]
    elif saw_flag:
        cleaned["source"] = ""

    if isinstance(raw_value_hex, str):
        cleaned["raw_value_hex"] = raw_value_hex[:18]
    elif saw_flag:
        cleaned["raw_value_hex"] = ""

    if not saw_flag and not any(
        isinstance(value, str) and value
        for value in (last_updated, source, raw_value_hex)
    ):
        return None

    return cleaned


def merge_throttle_state(previous: Any, current: dict, *, rebooted: bool) -> dict:
    """Preserve sticky bits until reboot, even if a later heartbeat omits them."""
    if rebooted or not isinstance(previous, dict):
        return dict(current)

    merged = dict(current)
    for field in (
        "under_voltage_sticky",
        "frequency_capped_sticky",
        "throttled_sticky",
        "soft_temp_limit_sticky",
    ):
        if previous.get(field):
            merged[field] = True
    return merged


def sticky_transition_labels(previous: Any, current: Any) -> list[str]:
    """Return friendly labels for sticky throttle bits newly set in ``current``."""
    if not isinstance(current, dict):
        return []

    previous = previous if isinstance(previous, dict) else {}
    labels: list[str] = []
    for field, label in _STICKY_LABELS:
        if bool(current.get(field)) and not bool(previous.get(field)):
            labels.append(label)
    return labels


def summarize_throttle_state(throttle_state: Any) -> dict | None:
    """Return a derived summary for UI/alerts, or None when clear."""
    if not isinstance(throttle_state, dict):
        return None

    current_labels = [
        label for field, label in _CURRENT_LABELS if bool(throttle_state.get(field))
    ]
    sticky_labels = [
        label
        for field, label in _STICKY_LABELS
        if bool(throttle_state.get(field)) and label not in current_labels
    ]

    if not current_labels and not sticky_labels:
        return None

    severity = "warning"
    if bool(throttle_state.get("under_voltage_now")):
        severity = "critical"
    elif bool(throttle_state.get("throttled_now")) or bool(
        throttle_state.get("frequency_capped_now")
    ):
        severity = "error"

    if current_labels:
        badge_text = " + ".join(current_labels)
        alert_message = "Raspberry Pi throttling: " + badge_text
        hint = (
            "Current Raspberry Pi throttling detected. Check the power supply, "
            "USB cable, and cooling. `vcgencmd get_throttled` on the camera "
            "shows the raw bit-flags."
        )
        if sticky_labels:
            hint += (
                " Sticky bits also set since boot: " + ", ".join(sticky_labels) + "."
            )
    else:
        badge_text = "Since boot: " + " + ".join(sticky_labels)
        alert_message = "Raspberry Pi throttling detected since boot: " + " + ".join(
            sticky_labels
        )
        hint = (
            "Sticky Raspberry Pi throttle bits were set since the last reboot. "
            "Check the power supply, USB cable, and cooling before the next "
            "high-load period."
        )

    return {
        "severity": severity,
        "state": "red" if severity == "critical" else "amber",
        "badge_text": badge_text,
        "alert_message": alert_message,
        "hint": hint,
        "labels": current_labels or sticky_labels,
        "last_updated": throttle_state.get("last_updated") or "",
        "source": throttle_state.get("source") or "",
        "raw_value_hex": throttle_state.get("raw_value_hex") or "",
    }
