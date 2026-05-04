# REQ: SWR-024, SWR-032; RISK: RISK-012, RISK-015; SEC: SC-012, SC-020; TEST: TC-023, TC-029
"""
Time health service — derived clock-integrity state for the dashboard.

Builds on SettingsService's existing timedatectl access and the heartbeat
timestamps already sent by cameras. This service stays pure application logic:
no Flask imports and no direct subprocess calls.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

log = logging.getLogger("monitor.time_health")


# ---------------------------------------------------------------------------
# Thresholds (LOCKED — see ADR-0018). Changing these degrades user trust in
# the status-strip colour. Any change must go through a new ADR.
# ---------------------------------------------------------------------------

DRIFT_AMBER_SECONDS = 2.0
DRIFT_RED_SECONDS = 30.0
SERVER_RED_SECONDS = 30 * 60
MAX_DRIFT_SECONDS_REPORTED = 3600.0
HYSTERESIS_SECONDS = 0.5
SERVER_RESYNC_IDEMPOTENCY_SECONDS = 60


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        return None


def _worst(*states: str) -> str:
    order = {"green": 0, "amber": 1, "red": 2}
    worst = "green"
    for state in states:
        if order.get(state, -1) > order[worst]:
            worst = state
    return worst


def _time_status_unavailable(status: dict) -> bool:
    return (
        not status.get("ntp_active")
        and not status.get("ntp_synchronized")
        and not status.get("system_time")
        and not status.get("rtc_time")
    )


def _clamp_drift(seconds: float) -> float:
    if seconds > MAX_DRIFT_SECONDS_REPORTED:
        return MAX_DRIFT_SECONDS_REPORTED
    if seconds < -MAX_DRIFT_SECONDS_REPORTED:
        return -MAX_DRIFT_SECONDS_REPORTED
    return seconds


def _camera_state_with_hysteresis(previous: str, magnitude: float) -> str:
    if previous == "red":
        if magnitude >= DRIFT_RED_SECONDS - HYSTERESIS_SECONDS:
            return "red"
        if magnitude >= DRIFT_AMBER_SECONDS - HYSTERESIS_SECONDS:
            return "amber"
        return "green"
    if previous == "amber":
        if magnitude >= DRIFT_RED_SECONDS:
            return "red"
        if magnitude >= DRIFT_AMBER_SECONDS - HYSTERESIS_SECONDS:
            return "amber"
        return "green"
    if magnitude >= DRIFT_RED_SECONDS:
        return "red"
    if magnitude >= DRIFT_AMBER_SECONDS:
        return "amber"
    return "green"


class TimeHealthService:
    """Compute clock-integrity state and queue operator-triggered resyncs."""

    def __init__(self, *, store, settings_service):
        self._store = store
        self._settings = settings_service
        self._camera_states: dict[str, str] = {}
        self._server_unsynced_since: datetime | None = None
        self._last_server_resync_at: datetime | None = None

    def compute_health(self) -> dict:
        """Return the current time-health payload. Never raises."""
        now = _utcnow()
        result = {
            "state": "green",
            "server": {
                "ntp_active": False,
                "ntp_synchronized": False,
                "unsynced_seconds": None,
                "last_sync_time": "",
            },
            "cameras": [],
            "worst_camera": None,
            "worst_drift_seconds": None,
        }

        server_state = "unknown"
        try:
            time_status = self._settings.get_time_status() or {}
        except Exception as exc:
            log.warning("time_health: get_time_status failed: %s", exc)
            time_status = {}
        try:
            timesync_status = self._settings.get_timesync_status() or {}
        except Exception as exc:
            log.warning("time_health: get_timesync_status failed: %s", exc)
            timesync_status = {}

        try:
            server_state, result["server"] = self._server_health(
                time_status, timesync_status, now
            )
        except Exception as exc:
            log.warning("time_health: server health failed: %s", exc)
            server_state = "unknown"

        camera_states: dict[str, str] = {}
        worst_camera_state = "green"
        try:
            cameras = self._store.get_cameras()
        except Exception as exc:
            log.warning("time_health: get_cameras failed: %s", exc)
            cameras = []

        for camera in cameras:
            if getattr(camera, "status", "") == "pending":
                continue
            camera_id = getattr(camera, "id", "")
            camera_name = getattr(camera, "name", "") or camera_id
            try:
                state, drift_seconds = self._camera_health(camera)
            except Exception as exc:
                log.warning("time_health: camera %s failed: %s", camera_id, exc)
                state, drift_seconds = "unknown", None
            result["cameras"].append(
                {
                    "id": camera_id,
                    "name": camera_name,
                    "drift_seconds": drift_seconds,
                    "state": state,
                }
            )
            if state in {"green", "amber", "red"}:
                camera_states[camera_id] = state
            if state not in {"amber", "red"} or drift_seconds is None:
                continue
            if worst_camera_state != "red" and state == "red":
                worst_camera_state = "red"
                result["worst_camera"] = camera_name
                result["worst_drift_seconds"] = drift_seconds
                continue
            current = result["worst_drift_seconds"]
            if current is None or abs(drift_seconds) > abs(current):
                worst_camera_state = state
                result["worst_camera"] = camera_name
                result["worst_drift_seconds"] = drift_seconds

        self._camera_states = camera_states

        known_states = [
            state
            for state in (server_state, worst_camera_state)
            if state in {"green", "amber", "red"}
        ]
        if known_states:
            result["state"] = _worst(*known_states)
        else:
            result["state"] = "unknown"
        if server_state == "unknown" and result["state"] == "green":
            result["state"] = "unknown"
        return result

    def request_resync(self, target: str) -> tuple[str, int, bool]:
        """Request a server or camera timesync restart.

        Returns ``(message, status_code, should_audit)``.
        """
        target = (target or "").strip()
        if not target:
            return "target is required", 400, False
        if target == "server":
            now = _utcnow()
            if (
                self._last_server_resync_at is not None
                and (now - self._last_server_resync_at).total_seconds()
                < SERVER_RESYNC_IDEMPOTENCY_SECONDS
            ):
                return "already queued", 200, False
            message, status = self._settings.restart_timesyncd()
            if status == 200:
                self._last_server_resync_at = now
                return message, status, True
            return message, status, False

        try:
            camera = self._store.get_camera(target)
        except Exception as exc:
            log.warning("time_health: get_camera(%s) failed: %s", target, exc)
            return "Failed to queue time resync", 500, False
        if not camera:
            return "Camera not found", 404, False

        pending = dict(getattr(camera, "pending_config", {}) or {})
        if pending.get("time_resync") is True:
            return "already queued", 200, False
        pending["time_resync"] = True
        camera.pending_config = pending
        try:
            self._store.save_camera(camera)
        except Exception as exc:
            log.warning("time_health: save_camera(%s) failed: %s", target, exc)
            return "Failed to queue time resync", 500, False
        return "Time resync queued", 200, True

    def _server_health(
        self, time_status: dict, timesync_status: dict, now: datetime
    ) -> tuple[str, dict]:
        detail = {
            "ntp_active": bool(time_status.get("ntp_active")),
            "ntp_synchronized": bool(time_status.get("ntp_synchronized")),
            "unsynced_seconds": None,
            "last_sync_time": str(timesync_status.get("last_sync_time", "") or ""),
        }
        if _time_status_unavailable(time_status):
            self._server_unsynced_since = None
            return "unknown", detail
        if detail["ntp_synchronized"]:
            self._server_unsynced_since = None
            return "green", detail
        if detail["ntp_active"]:
            if self._server_unsynced_since is None:
                self._server_unsynced_since = now
            unsynced = int((now - self._server_unsynced_since).total_seconds())
            detail["unsynced_seconds"] = max(0, unsynced)
            if unsynced >= SERVER_RED_SECONDS:
                return "red", detail
            return "amber", detail
        self._server_unsynced_since = None
        return "green", detail

    def _camera_health(self, camera) -> tuple[str, float | None]:
        if getattr(camera, "status", "") == "offline":
            self._camera_states.pop(getattr(camera, "id", ""), None)
            return "unknown", None
        last_seen = _parse_ts(getattr(camera, "last_seen", None))
        beat_seen = _parse_ts(getattr(camera, "last_beat_camera_ts", None))
        if last_seen is None or beat_seen is None:
            self._camera_states.pop(getattr(camera, "id", ""), None)
            return "unknown", None
        drift = _clamp_drift((last_seen - beat_seen).total_seconds())
        magnitude = abs(drift)
        previous = self._camera_states.get(getattr(camera, "id", ""), "green")
        state = _camera_state_with_hysteresis(previous, magnitude)
        self._camera_states[getattr(camera, "id", "")] = state
        return state, round(drift, 1)
