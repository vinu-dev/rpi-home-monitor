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

    return SystemSummaryService(
        store=store,
        storage_manager=storage,
        audit=audit,
        recordings_service=rec_svc,
        health_module=health,
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
