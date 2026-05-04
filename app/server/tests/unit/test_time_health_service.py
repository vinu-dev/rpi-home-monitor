# REQ: SWR-024, SWR-032; RISK: RISK-012, RISK-015; SEC: SC-012, SC-020; TEST: TC-023, TC-029
"""Unit tests for TimeHealthService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import monitor.services.time_health_service as time_health_module
from monitor.services.time_health_service import (
    DRIFT_AMBER_SECONDS,
    DRIFT_RED_SECONDS,
    SERVER_RED_SECONDS,
    TimeHealthService,
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _camera(**overrides):
    now = datetime.now(UTC)
    defaults = {
        "id": "cam-001",
        "name": "Front Door",
        "status": "online",
        "last_seen": _iso(now),
        "last_beat_camera_ts": _iso(now),
        "pending_config": {},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _service(*, cameras=None, time_status=None, timesync_status=None):
    store = MagicMock()
    store.get_cameras.return_value = cameras or []
    store.get_camera.return_value = None
    settings = MagicMock()
    settings.get_time_status.return_value = time_status or {
        "ntp_active": True,
        "ntp_synchronized": True,
        "system_time": "Sat 2026-05-04 12:00:00 UTC",
        "rtc_time": "Sat 2026-05-04 12:00:00 UTC",
    }
    settings.get_timesync_status.return_value = timesync_status or {
        "last_sync_time": ""
    }
    return TimeHealthService(store=store, settings_service=settings), store, settings


class TestComputeHealth:
    def test_returns_green_when_server_and_cameras_are_in_sync(self):
        now = datetime.now(UTC)
        svc, _, _ = _service(
            cameras=[_camera(last_seen=_iso(now), last_beat_camera_ts=_iso(now))]
        )

        result = svc.compute_health()

        assert result["state"] == "green"
        assert result["server"]["ntp_synchronized"] is True
        assert result["cameras"][0]["state"] == "green"
        assert result["cameras"][0]["drift_seconds"] == 0.0

    def test_camera_drift_is_sign_aware_and_amber(self):
        now = datetime.now(UTC)
        svc, _, _ = _service(
            cameras=[
                _camera(
                    name="Living Room",
                    last_seen=_iso(now),
                    last_beat_camera_ts=_iso(now - timedelta(seconds=4)),
                )
            ]
        )

        result = svc.compute_health()

        assert result["state"] == "amber"
        assert result["worst_camera"] == "Living Room"
        assert result["worst_drift_seconds"] == 4.0
        assert result["cameras"][0]["state"] == "amber"

    def test_negative_drift_is_preserved(self):
        now = datetime.now(UTC)
        svc, _, _ = _service(
            cameras=[
                _camera(
                    last_seen=_iso(now),
                    last_beat_camera_ts=_iso(now + timedelta(seconds=3.0)),
                )
            ]
        )

        result = svc.compute_health()

        assert result["cameras"][0]["drift_seconds"] == -3.0
        assert result["cameras"][0]["state"] == "amber"

    def test_offline_camera_is_reported_unknown_without_affecting_state(self):
        now = datetime.now(UTC)
        svc, _, _ = _service(
            cameras=[
                _camera(
                    status="offline",
                    last_seen=_iso(now),
                    last_beat_camera_ts=_iso(now - timedelta(seconds=40)),
                )
            ]
        )

        result = svc.compute_health()

        assert result["state"] == "green"
        assert result["cameras"][0]["state"] == "unknown"
        assert result["cameras"][0]["drift_seconds"] is None

    def test_missing_timedatectl_defaults_degrade_to_unknown(self):
        svc, _, settings = _service(
            time_status={
                "ntp_active": False,
                "ntp_synchronized": False,
                "system_time": "",
                "rtc_time": "",
            }
        )
        settings.get_timesync_status.return_value = {"last_sync_time": ""}

        result = svc.compute_health()

        assert result["state"] == "unknown"
        assert result["server"]["ntp_active"] is False

    def test_amber_hysteresis_requires_clear_margin(self):
        now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
        svc, store, _ = _service(
            cameras=[
                _camera(
                    last_seen=_iso(now),
                    last_beat_camera_ts=_iso(
                        now - timedelta(seconds=DRIFT_AMBER_SECONDS)
                    ),
                )
            ]
        )
        assert svc.compute_health()["cameras"][0]["state"] == "amber"

        store.get_cameras.return_value = [
            _camera(
                last_seen=_iso(now),
                last_beat_camera_ts=_iso(
                    now - timedelta(seconds=DRIFT_AMBER_SECONDS - 0.1)
                ),
            )
        ]
        assert svc.compute_health()["cameras"][0]["state"] == "amber"

        store.get_cameras.return_value = [
            _camera(
                last_seen=_iso(now),
                last_beat_camera_ts=_iso(
                    now - timedelta(seconds=DRIFT_AMBER_SECONDS - 1.0)
                ),
            )
        ]
        assert svc.compute_health()["cameras"][0]["state"] == "green"

    def test_red_hysteresis_drops_to_amber_before_green(self):
        now = datetime.now(UTC)
        svc, store, _ = _service(
            cameras=[
                _camera(
                    last_seen=_iso(now),
                    last_beat_camera_ts=_iso(
                        now - timedelta(seconds=DRIFT_RED_SECONDS)
                    ),
                )
            ]
        )
        assert svc.compute_health()["cameras"][0]["state"] == "red"

        store.get_cameras.return_value = [
            _camera(
                last_seen=_iso(now),
                last_beat_camera_ts=_iso(
                    now - timedelta(seconds=DRIFT_RED_SECONDS - 1.0)
                ),
            )
        ]
        assert svc.compute_health()["cameras"][0]["state"] == "amber"

    def test_server_unsynced_turns_red_after_threshold(self, monkeypatch):
        base = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        moments = [base, base + timedelta(seconds=SERVER_RED_SECONDS + 1)]

        def fake_utcnow():
            return moments.pop(0)

        monkeypatch.setattr(time_health_module, "_utcnow", fake_utcnow)
        svc, _, _ = _service(
            time_status={
                "ntp_active": True,
                "ntp_synchronized": False,
                "system_time": "now",
                "rtc_time": "now",
            }
        )

        first = svc.compute_health()
        second = svc.compute_health()

        assert first["state"] == "amber"
        assert second["state"] == "red"
        assert second["server"]["unsynced_seconds"] >= SERVER_RED_SECONDS

    def test_compute_health_never_raises_when_settings_fail(self):
        svc, _, settings = _service()
        settings.get_time_status.side_effect = RuntimeError("boom")
        settings.get_timesync_status.side_effect = RuntimeError("boom")

        result = svc.compute_health()

        assert result["state"] == "unknown"
        assert result["cameras"] == []


class TestRequestResync:
    def test_missing_target_returns_400(self):
        svc, _, _ = _service()

        message, status, should_audit = svc.request_resync("")

        assert status == 400
        assert should_audit is False
        assert "target" in message

    def test_server_resync_is_idempotent_for_sixty_seconds(self, monkeypatch):
        base = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        moments = [base, base + timedelta(seconds=30)]

        def fake_utcnow():
            return moments.pop(0)

        monkeypatch.setattr(time_health_module, "_utcnow", fake_utcnow)
        svc, _, settings = _service()
        settings.restart_timesyncd.return_value = ("System time resync requested", 200)

        first = svc.request_resync("server")
        second = svc.request_resync("server")

        assert first == ("System time resync requested", 200, True)
        assert second == ("already queued", 200, False)

    def test_camera_resync_sets_pending_flag_once(self):
        camera = _camera()
        svc, store, _ = _service()
        store.get_camera.return_value = camera

        first = svc.request_resync(camera.id)
        second = svc.request_resync(camera.id)

        assert first == ("Time resync queued", 200, True)
        assert second == ("already queued", 200, False)
        assert camera.pending_config["time_resync"] is True

    def test_unknown_camera_returns_404(self):
        svc, _, _ = _service()

        message, status, should_audit = svc.request_resync("cam-missing")

        assert status == 404
        assert should_audit is False
        assert "Camera not found" == message
