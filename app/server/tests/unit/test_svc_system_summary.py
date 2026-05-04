# REQ: SWR-032; RISK: RISK-015; SEC: SC-020; TEST: TC-029
"""
Unit tests for SystemSummaryService (ADR-0018).

Each test exercises one threshold transition so that the aggregator's
colour contract is pinned — the ADR locks these thresholds for user
muscle-memory reasons, so regressions here are silent trust breakers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from monitor.services.system_summary_service import (
    CAMERA_OFFLINE_AMBER_SECONDS,
    CAMERA_OFFLINE_RED_SECONDS,
    CPU_AMBER_PERCENT,
    CPU_TEMP_AMBER_C,
    CPU_TEMP_RED_C,
    DISK_AMBER_PERCENT,
    DISK_RED_PERCENT,
    MEMORY_AMBER_PERCENT,
    SystemSummaryService,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cam(**kwargs):
    """Build a minimal Camera-like namespace for store.get_cameras()."""
    defaults = {
        "id": "cam-1",
        "name": "Cam 1",
        "status": "online",
        "last_seen": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _build(**overrides):
    """Build a SystemSummaryService with mocked dependencies."""
    store = MagicMock()
    store.get_cameras.return_value = overrides.get("cameras", [_cam()])
    store.get_camera.return_value = None

    storage = MagicMock()
    storage.get_storage_stats.return_value = overrides.get(
        "stats",
        {
            "percent": 10.0,
            "free_gb": 100,
            "total_gb": 200,
            "recordings_dir": "/tmp/empty-dir-that-does-not-exist",
        },
    )

    audit = MagicMock()
    audit.get_events.return_value = overrides.get("events", [])

    rec_svc = MagicMock()
    rec_svc.default_recordings_dir = "/tmp"

    health = MagicMock()
    health.get_health_summary.return_value = overrides.get(
        "health_summary",
        {
            "cpu_temp_c": 45.0,
            "cpu_usage_percent": 5.0,
            "memory": {"percent": 30.0},
        },
    )
    time_health = MagicMock()
    time_health.compute_health.return_value = overrides.get(
        "time_health",
        {
            "state": "green",
            "server": {
                "ntp_active": True,
                "ntp_synchronized": True,
                "unsynced_seconds": None,
                "last_sync_time": "",
            },
            "cameras": [],
            "worst_camera": None,
            "worst_drift_seconds": None,
        },
    )

    return SystemSummaryService(
        store=store,
        storage_manager=storage,
        audit=audit,
        recordings_service=rec_svc,
        health_module=health,
        time_health=time_health,
    )


def _iso_ago(seconds: float) -> str:
    """Return an ISO-8601 timestamp `seconds` ago."""
    return (
        (datetime.now(UTC) - timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Green baseline
# ---------------------------------------------------------------------------


class TestGreenBaseline:
    def test_all_quiet_returns_green(self):
        svc = _build()
        out = svc.compute_summary()
        assert out["state"] == "green"
        assert "All systems normal" in out["summary"]
        assert out["deep_link"] == "/"
        assert out["details"]["cameras"]["online"] == 1
        assert out["details"]["cameras"]["total"] == 1
        assert out["details"]["recent_errors"] == 0

    def test_pending_cameras_excluded_from_totals(self):
        svc = _build(
            cameras=[_cam(status="online"), _cam(id="cam-2", status="pending")]
        )
        out = svc.compute_summary()
        assert out["details"]["cameras"]["total"] == 1

    def test_time_health_unknown_does_not_flip_summary(self):
        svc = _build(
            time_health={
                "state": "unknown",
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
        )
        out = svc.compute_summary()
        assert out["state"] == "green"
        assert out["details"]["time_health"]["state"] == "unknown"


# ---------------------------------------------------------------------------
# Storage thresholds
# ---------------------------------------------------------------------------


class TestStorageStateTransitions:
    def test_disk_below_amber_stays_green(self):
        svc = _build(
            stats={
                "percent": DISK_AMBER_PERCENT - 0.1,
                "free_gb": 10,
                "total_gb": 100,
                "recordings_dir": "",
            }
        )
        assert svc.compute_summary()["state"] == "green"

    def test_disk_at_amber_boundary_is_amber(self):
        svc = _build(
            stats={
                "percent": DISK_AMBER_PERCENT,
                "free_gb": 5,
                "total_gb": 100,
                "recordings_dir": "",
            }
        )
        out = svc.compute_summary()
        assert out["state"] == "amber"
        assert "disk" in out["summary"].lower()

    def test_disk_at_red_boundary_is_red(self):
        svc = _build(
            stats={
                "percent": DISK_RED_PERCENT,
                "free_gb": 1,
                "total_gb": 100,
                "recordings_dir": "",
            }
        )
        out = svc.compute_summary()
        assert out["state"] == "red"
        assert out["deep_link"].startswith("/settings")


# ---------------------------------------------------------------------------
# Camera thresholds
# ---------------------------------------------------------------------------


class TestCameraStateTransitions:
    def test_recently_offline_is_amber(self):
        cam = _cam(
            status="offline", last_seen=_iso_ago(CAMERA_OFFLINE_AMBER_SECONDS + 5)
        )
        svc = _build(cameras=[cam])
        out = svc.compute_summary()
        assert out["state"] == "amber"
        assert "offline" in out["summary"].lower()
        # Deep-link points at /dashboard with the camera id as fragment
        # so clicking the status strip on the dashboard scrolls to
        # the offending card without round-tripping through `/` (which
        # 302s and drops the fragment). Regression for the
        # "click does nothing" bug reported live.
        assert out["deep_link"].startswith("/dashboard#camera-")
        assert cam.id in out["deep_link"]

    def test_long_offline_is_red(self):
        cam = _cam(
            status="offline", last_seen=_iso_ago(CAMERA_OFFLINE_RED_SECONDS + 60)
        )
        svc = _build(cameras=[cam])
        out = svc.compute_summary()
        assert out["state"] == "red"

    def test_never_seen_offline_is_red(self):
        cam = _cam(status="offline", last_seen=None)
        svc = _build(cameras=[cam])
        assert svc.compute_summary()["state"] == "red"

    def test_single_offline_summary_names_camera(self):
        cam = _cam(
            id="cam-front", name="Front Door", status="offline", last_seen=_iso_ago(120)
        )
        svc = _build(cameras=[cam])
        assert "Front Door" in svc.compute_summary()["summary"]

    def test_multi_offline_summary_counts(self):
        cams = [
            _cam(id="cam-1", name="Front", status="offline", last_seen=_iso_ago(120)),
            _cam(id="cam-2", name="Back", status="offline", last_seen=_iso_ago(120)),
        ]
        svc = _build(cameras=cams)
        assert "2 cameras are offline" in svc.compute_summary()["summary"]

    def test_sticky_throttle_is_amber_and_names_camera(self):
        cam = _cam(
            id="cam-front",
            name="Front Door",
            throttle_state={
                "under_voltage_now": False,
                "under_voltage_sticky": True,
                "frequency_capped_now": False,
                "frequency_capped_sticky": False,
                "throttled_now": False,
                "throttled_sticky": False,
                "soft_temp_limit_now": False,
                "soft_temp_limit_sticky": False,
                "last_updated": "2026-05-04T12:00:00Z",
                "source": "vcgencmd",
                "raw_value_hex": "0x00010000",
            },
        )
        svc = _build(cameras=[cam])
        out = svc.compute_summary()
        assert out["state"] == "amber"
        assert "Front Door is throttled" in out["summary"]
        assert out["details"]["cameras"]["throttled_names"] == ["Front Door"]

    def test_current_under_voltage_is_red(self):
        cam = _cam(
            id="cam-front",
            name="Front Door",
            throttle_state={
                "under_voltage_now": True,
                "under_voltage_sticky": True,
                "frequency_capped_now": True,
                "frequency_capped_sticky": True,
                "throttled_now": False,
                "throttled_sticky": False,
                "soft_temp_limit_now": False,
                "soft_temp_limit_sticky": False,
                "last_updated": "2026-05-04T12:00:00Z",
                "source": "vcgencmd",
                "raw_value_hex": "0x00030003",
            },
        )
        svc = _build(cameras=[cam])
        out = svc.compute_summary()
        assert out["state"] == "red"
        assert out["details"]["cameras"]["state"] == "red"


# ---------------------------------------------------------------------------
# Recorder host thresholds
# ---------------------------------------------------------------------------


class TestRecorderHostTransitions:
    def test_cpu_above_amber_flips_amber(self):
        svc = _build(
            health_summary={
                "cpu_temp_c": 40,
                "cpu_usage_percent": CPU_AMBER_PERCENT + 5,
                "memory": {"percent": 10},
            }
        )
        assert svc.compute_summary()["state"] == "amber"

    def test_temp_amber_flips_amber(self):
        svc = _build(
            health_summary={
                "cpu_temp_c": CPU_TEMP_AMBER_C + 1,
                "cpu_usage_percent": 5,
                "memory": {"percent": 10},
            }
        )
        assert svc.compute_summary()["state"] == "amber"

    def test_temp_red_flips_red(self):
        svc = _build(
            health_summary={
                "cpu_temp_c": CPU_TEMP_RED_C + 1,
                "cpu_usage_percent": 5,
                "memory": {"percent": 10},
            }
        )
        assert svc.compute_summary()["state"] == "red"

    def test_memory_amber_flips_amber(self):
        svc = _build(
            health_summary={
                "cpu_temp_c": 40,
                "cpu_usage_percent": 5,
                "memory": {"percent": MEMORY_AMBER_PERCENT + 1},
            }
        )
        assert svc.compute_summary()["state"] == "amber"


# ---------------------------------------------------------------------------
# Audit event surfacing
# ---------------------------------------------------------------------------


class TestRecentErrors:
    def test_error_in_window_flips_red(self):
        events = [
            {"timestamp": _iso_ago(60), "event": "OTA_FAILED", "detail": ""},
        ]
        svc = _build(events=events)
        out = svc.compute_summary()
        assert out["state"] == "red"
        assert "event" in out["summary"].lower() or "log" in out["summary"].lower()

    def test_warning_in_window_flips_amber(self):
        events = [
            {"timestamp": _iso_ago(60), "event": "CAMERA_OFFLINE", "detail": ""},
        ]
        svc = _build(events=events)
        assert svc.compute_summary()["state"] == "amber"

    def test_old_errors_are_ignored(self):
        events = [
            {"timestamp": _iso_ago(3600 * 3), "event": "OTA_FAILED", "detail": ""},
        ]
        svc = _build(events=events)
        assert svc.compute_summary()["state"] == "green"

    def test_login_failed_storm_does_not_flip_colour(self):
        # ADR-0018: LOGIN_FAILED is intentionally excluded from the status
        # strip so a password-spray attempt doesn't turn the dashboard red.
        events = [
            {"timestamp": _iso_ago(30), "event": "LOGIN_FAILED", "detail": ""}
            for _ in range(50)
        ]
        svc = _build(events=events)
        assert svc.compute_summary()["state"] == "green"


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_store_exception_does_not_raise(self):
        store = MagicMock()
        store.get_cameras.side_effect = RuntimeError("boom")

        svc = SystemSummaryService(
            store=store,
            storage_manager=MagicMock(
                get_storage_stats=lambda: {
                    "percent": 10,
                    "free_gb": 10,
                    "total_gb": 100,
                    "recordings_dir": "",
                }
            ),
            audit=MagicMock(get_events=lambda **_: []),
            recordings_service=MagicMock(default_recordings_dir="/tmp"),
            health_module=MagicMock(
                get_health_summary=lambda _: {
                    "cpu_temp_c": 40,
                    "cpu_usage_percent": 5,
                    "memory": {"percent": 10},
                }
            ),
            time_health=MagicMock(
                compute_health=lambda: {
                    "state": "green",
                    "server": {},
                    "cameras": [],
                    "worst_camera": None,
                    "worst_drift_seconds": None,
                }
            ),
        )
        out = svc.compute_summary()
        assert out["state"] == "green"
        assert out["details"]["cameras"]["total"] == 0

    def test_all_services_failing_does_not_raise(self):
        store = MagicMock()
        store.get_cameras.side_effect = RuntimeError()
        storage = MagicMock()
        storage.get_storage_stats.side_effect = RuntimeError()
        audit = MagicMock()
        audit.get_events.side_effect = RuntimeError()
        rec_svc = MagicMock(default_recordings_dir="/tmp")
        health = MagicMock()
        health.get_health_summary.side_effect = RuntimeError()

        svc = SystemSummaryService(
            store=store,
            storage_manager=storage,
            audit=audit,
            recordings_service=rec_svc,
            health_module=health,
            time_health=MagicMock(
                compute_health=lambda: {
                    "state": "green",
                    "server": {},
                    "cameras": [],
                    "worst_camera": None,
                    "worst_drift_seconds": None,
                }
            ),
        )
        out = svc.compute_summary()
        # Must not crash; best-effort green with zeros.
        assert out["state"] in {"green", "amber", "red"}
        assert "summary" in out


# ---------------------------------------------------------------------------
# Priority ordering — worst signal wins
# ---------------------------------------------------------------------------


class TestPriority:
    def test_red_from_error_overrides_disk_amber(self):
        svc = _build(
            stats={
                "percent": DISK_AMBER_PERCENT + 1,
                "free_gb": 5,
                "total_gb": 100,
                "recordings_dir": "",
            },
            events=[{"timestamp": _iso_ago(30), "event": "OTA_FAILED", "detail": ""}],
        )
        assert svc.compute_summary()["state"] == "red"

    def test_red_disk_overrides_amber_camera(self):
        svc = _build(
            cameras=[_cam(status="offline", last_seen=_iso_ago(120))],
            stats={
                "percent": DISK_RED_PERCENT + 1,
                "free_gb": 1,
                "total_gb": 100,
                "recordings_dir": "",
            },
        )
        assert svc.compute_summary()["state"] == "red"

    def test_time_health_camera_sentence_is_used_when_it_dominates(self):
        svc = _build(
            time_health={
                "state": "amber",
                "server": {
                    "ntp_active": True,
                    "ntp_synchronized": True,
                    "unsynced_seconds": None,
                    "last_sync_time": "",
                },
                "cameras": [
                    {
                        "id": "cam-1",
                        "name": "Living Room",
                        "drift_seconds": 4.2,
                        "state": "amber",
                    }
                ],
                "worst_camera": "Living Room",
                "worst_drift_seconds": 4.2,
            }
        )
        out = svc.compute_summary()
        assert out["state"] == "amber"
        assert out["summary"] == "Camera Living Room clock drifted +4.2s — resync"
        assert out["deep_link"] == "/settings#time-health"

    def test_time_health_server_sentence_is_used_when_it_dominates(self):
        svc = _build(
            time_health={
                "state": "red",
                "server": {
                    "ntp_active": True,
                    "ntp_synchronized": False,
                    "unsynced_seconds": 1900,
                    "last_sync_time": "",
                },
                "cameras": [],
                "worst_camera": None,
                "worst_drift_seconds": None,
            }
        )
        out = svc.compute_summary()
        assert out["state"] == "red"
        assert out["summary"] == "Server time not synchronized — resync"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
