"""Tests for AlertCenterService — ADR-0024 derive-on-read alert center."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock

from monitor.services.alert_center_service import (
    ALERT_AUDIT_EVENTS,
    ALERT_FAULT_SEVERITIES,
    AlertCenterService,
    _audit_message,
    _audit_subject,
    _looks_like_alert_id,
    _normalise_iso_z,
)

# ---------------------------------------------------------------------------
# Test fixtures — minimal stubs so tests don't pull the whole app factory
# ---------------------------------------------------------------------------


@dataclass
class _FakeMotionEvent:
    id: str
    camera_id: str
    started_at: str
    ended_at: str | None = None
    peak_score: float = 0.0
    duration_seconds: float = 0.0


@dataclass
class _FakeCamera:
    id: str
    hardware_faults: list[dict] = field(default_factory=list)


def _make_service(tmp_path, *, cameras=None, audit_events=None, motion_events=None):
    """Build an AlertCenterService backed by mocked sources."""
    store = MagicMock()
    store.get_cameras.return_value = cameras or []

    audit = MagicMock()
    audit.get_events.return_value = audit_events or []

    motion = MagicMock()
    motion.list_events.return_value = motion_events or []

    return AlertCenterService(
        store=store,
        audit_logger=audit,
        motion_event_store=motion,
        read_state_path=str(tmp_path / "config" / "alert_read_state.json"),
    )


# ---------------------------------------------------------------------------
# Catalogue + helper unit tests
# ---------------------------------------------------------------------------


class TestCatalogue:
    def test_audit_catalogue_excludes_login_failed(self):
        # ADR-0024: LOGIN_FAILED stays in the audit teaser, not the
        # alert center. Regression test for the explicit decision.
        assert "LOGIN_FAILED" not in ALERT_AUDIT_EVENTS

    def test_audit_catalogue_includes_critical_codes(self):
        for code in ("OTA_FAILED", "OTA_ROLLBACK", "CAMERA_OFFLINE", "CERT_REVOKED"):
            assert code in ALERT_AUDIT_EVENTS

    def test_audit_catalogue_includes_storage_health_codes(self):
        """#140 — STORAGE_LOW and RETENTION_RISK plug into the same
        alert catalogue. Regression: if these were dropped a future
        storage outage would silently disappear from the inbox."""
        assert "STORAGE_LOW" in ALERT_AUDIT_EVENTS
        assert "RETENTION_RISK" in ALERT_AUDIT_EVENTS

    def test_fault_severities_exclude_info(self):
        # info-level faults stay on the camera card; not alert-worthy.
        assert "info" not in ALERT_FAULT_SEVERITIES
        assert "warning" in ALERT_FAULT_SEVERITIES
        assert "error" in ALERT_FAULT_SEVERITIES
        assert "critical" in ALERT_FAULT_SEVERITIES


class TestAlertIdShape:
    def test_recognises_typed_prefixes(self):
        assert _looks_like_alert_id("fault:cam-d8ee:sensor_missing")
        assert _looks_like_alert_id("audit:abcdef0123456789")
        assert _looks_like_alert_id("motion:mot-20260430T071122Z-cam-d8ee")

    def test_rejects_garbage(self):
        assert not _looks_like_alert_id("")
        assert not _looks_like_alert_id("hello-world")
        assert not _looks_like_alert_id("UNKNOWN:foo")
        assert not _looks_like_alert_id(None)  # type: ignore[arg-type]


class TestTimestampNormalisation:
    def test_iso_with_offset_to_z(self):
        assert _normalise_iso_z("2026-04-30T08:14:02+00:00") == "2026-04-30T08:14:02Z"

    def test_already_z(self):
        assert _normalise_iso_z("2026-04-30T08:14:02Z") == "2026-04-30T08:14:02Z"

    def test_garbage_falls_through(self):
        assert _normalise_iso_z("not-a-timestamp") == "not-a-timestamp"

    def test_empty_returns_now(self):
        out = _normalise_iso_z("")
        # We don't pin the exact time but it must end with Z.
        assert out.endswith("Z")


class TestAuditSubjectInference:
    def test_camera_offline_with_camid_in_detail(self):
        subj = _audit_subject(
            {"event": "CAMERA_OFFLINE", "detail": "cam-d8ee gone offline"}
        )
        assert subj == {"type": "camera", "id": "cam-d8ee"}

    def test_camera_offline_without_camid_falls_back_to_server(self):
        subj = _audit_subject({"event": "CAMERA_OFFLINE", "detail": ""})
        assert subj == {"type": "server"}

    def test_ota_failed_anchors_on_server(self):
        subj = _audit_subject({"event": "OTA_FAILED", "detail": "verify failed"})
        assert subj == {"type": "server"}


class TestAuditMessage:
    def test_known_code_uses_label(self):
        assert "OTA update failed" in _audit_message(
            {"event": "OTA_FAILED", "detail": "verify failed"}
        )

    def test_unknown_code_falls_through(self):
        msg = _audit_message({"event": "MYSTERY_EVENT", "detail": ""})
        assert "Mystery event" in msg or "MYSTERY_EVENT" in msg


# ---------------------------------------------------------------------------
# Service-level behaviour
# ---------------------------------------------------------------------------


class TestComputeAlerts:
    """Test which alerts each role sees."""

    def test_admin_sees_all_three_sources(self, tmp_path):
        cameras = [
            _FakeCamera(
                id="cam-d8ee",
                hardware_faults=[
                    {
                        "code": "camera_sensor_missing",
                        "severity": "error",
                        "message": "Camera sensor missing",
                    }
                ],
            ),
        ]
        audit_events = [
            {
                "timestamp": "2026-04-30T08:14:02Z",
                "event": "OTA_FAILED",
                "user": "admin",
                "ip": "1.2.3.4",
                "detail": "verify failed",
            }
        ]
        motion_events = [
            _FakeMotionEvent(
                id="mot-1",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:00:00Z",
                ended_at="2026-04-30T08:00:15Z",
                peak_score=0.18,
            )
        ]
        svc = _make_service(
            tmp_path,
            cameras=cameras,
            audit_events=audit_events,
            motion_events=motion_events,
        )
        result = svc.list_alerts(user="alice", role="admin")
        sources = {a["source"] for a in result}
        assert sources == {"fault", "audit", "motion"}

    def test_viewer_does_not_see_audit_alerts(self, tmp_path):
        cameras = [
            _FakeCamera(
                id="cam-d8ee",
                hardware_faults=[
                    {
                        "code": "camera_sensor_missing",
                        "severity": "error",
                        "message": "Camera sensor missing",
                    }
                ],
            ),
        ]
        audit_events = [
            {
                "timestamp": "2026-04-30T08:14:02Z",
                "event": "OTA_FAILED",
                "user": "admin",
                "ip": "1.2.3.4",
                "detail": "verify failed",
            }
        ]
        motion_events = [
            _FakeMotionEvent(
                id="mot-1",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:00:00Z",
                ended_at="2026-04-30T08:00:15Z",
                peak_score=0.18,
            )
        ]
        svc = _make_service(
            tmp_path,
            cameras=cameras,
            audit_events=audit_events,
            motion_events=motion_events,
        )
        result = svc.list_alerts(user="bob", role="viewer")
        sources = {a["source"] for a in result}
        assert "audit" not in sources
        assert sources == {"fault", "motion"}


class TestFaultFiltering:
    def test_info_fault_excluded(self, tmp_path):
        cameras = [
            _FakeCamera(
                id="cam-d8ee",
                hardware_faults=[
                    {"code": "thermal_headroom", "severity": "info", "message": "warm"}
                ],
            )
        ]
        svc = _make_service(tmp_path, cameras=cameras)
        result = svc.list_alerts(user="alice", role="admin")
        assert result == []

    def test_warning_fault_included(self, tmp_path):
        cameras = [
            _FakeCamera(
                id="cam-d8ee",
                hardware_faults=[
                    {
                        "code": "h264_unsupported",
                        "severity": "warning",
                        "message": "no h264",
                    }
                ],
            )
        ]
        svc = _make_service(tmp_path, cameras=cameras)
        result = svc.list_alerts(user="alice", role="admin")
        assert len(result) == 1
        assert result[0]["severity"] == "warning"
        assert result[0]["id"] == "fault:cam-d8ee:h264_unsupported"

    def test_malformed_fault_skipped(self, tmp_path):
        cameras = [
            _FakeCamera(
                id="cam-d8ee",
                hardware_faults=["not-a-dict", {"code": "ok", "severity": "error"}],
            )
        ]
        svc = _make_service(tmp_path, cameras=cameras)
        result = svc.list_alerts(user="alice", role="admin")
        assert len(result) == 1


class TestAuditFiltering:
    def test_login_failed_excluded(self, tmp_path):
        # Regression: ADR-0024 explicitly keeps LOGIN_FAILED out.
        audit_events = [
            {
                "timestamp": "2026-04-30T08:00:00Z",
                "event": "LOGIN_FAILED",
                "user": "",
                "ip": "1.2.3.4",
                "detail": "wrong password",
            }
        ]
        svc = _make_service(tmp_path, audit_events=audit_events)
        result = svc.list_alerts(user="alice", role="admin")
        assert result == []

    def test_unknown_audit_code_excluded(self, tmp_path):
        audit_events = [
            {
                "timestamp": "2026-04-30T08:00:00Z",
                "event": "RANDOM_NEW_THING",
                "user": "",
                "ip": "",
                "detail": "",
            }
        ]
        svc = _make_service(tmp_path, audit_events=audit_events)
        result = svc.list_alerts(user="alice", role="admin")
        assert result == []


class TestMotionFiltering:
    def test_in_progress_event_excluded(self, tmp_path):
        # ended_at is None → still in progress, not yet alert-worthy.
        motion_events = [
            _FakeMotionEvent(
                id="mot-running",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:00:00Z",
                ended_at=None,
                peak_score=0.5,
            )
        ]
        svc = _make_service(tmp_path, motion_events=motion_events)
        result = svc.list_alerts(user="alice", role="admin")
        assert result == []

    def test_low_score_excluded(self, tmp_path):
        motion_events = [
            _FakeMotionEvent(
                id="mot-noise",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:00:00Z",
                ended_at="2026-04-30T08:00:02Z",
                peak_score=0.001,  # well below threshold
            )
        ]
        svc = _make_service(tmp_path, motion_events=motion_events)
        result = svc.list_alerts(user="alice", role="admin")
        assert result == []

    def test_severity_scales_with_peak_score(self, tmp_path):
        motion_events = [
            _FakeMotionEvent(
                id="mot-low",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:00:00Z",
                ended_at="2026-04-30T08:00:02Z",
                peak_score=0.07,  # >= threshold, < 0.10
            ),
            _FakeMotionEvent(
                id="mot-high",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:01:00Z",
                ended_at="2026-04-30T08:01:02Z",
                peak_score=0.18,
            ),
        ]
        svc = _make_service(tmp_path, motion_events=motion_events)
        result = svc.list_alerts(user="alice", role="admin")
        by_id = {a["id"]: a for a in result}
        assert by_id["motion:mot-low"]["severity"] == "info"
        assert by_id["motion:mot-high"]["severity"] == "warning"


class TestSorting:
    def test_newest_first(self, tmp_path):
        motion_events = [
            _FakeMotionEvent(
                id="mot-old",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:00:00Z",
                ended_at="2026-04-30T08:00:02Z",
                peak_score=0.18,
            ),
            _FakeMotionEvent(
                id="mot-new",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:05:00Z",
                ended_at="2026-04-30T08:05:02Z",
                peak_score=0.18,
            ),
        ]
        svc = _make_service(tmp_path, motion_events=motion_events)
        result = svc.list_alerts(user="alice", role="admin")
        assert [a["id"] for a in result] == ["motion:mot-new", "motion:mot-old"]


class TestFilters:
    def _two_camera_setup(self, tmp_path):
        cameras = [
            _FakeCamera(
                id="cam-d8ee",
                hardware_faults=[
                    {"code": "boom", "severity": "error", "message": "boom"}
                ],
            )
        ]
        audit_events = [
            {
                "timestamp": "2026-04-30T08:00:00Z",
                "event": "OTA_FAILED",
                "user": "admin",
                "ip": "",
                "detail": "",
            }
        ]
        motion_events = [
            _FakeMotionEvent(
                id="mot-1",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:01:00Z",
                ended_at="2026-04-30T08:01:02Z",
                peak_score=0.18,
            )
        ]
        return _make_service(
            tmp_path,
            cameras=cameras,
            audit_events=audit_events,
            motion_events=motion_events,
        )

    def test_source_filter(self, tmp_path):
        svc = self._two_camera_setup(tmp_path)
        result = svc.list_alerts(user="alice", role="admin", source="motion")
        assert len(result) == 1
        assert result[0]["source"] == "motion"

    def test_severity_at_least(self, tmp_path):
        svc = self._two_camera_setup(tmp_path)
        result = svc.list_alerts(user="alice", role="admin", severity="error")
        # only the fault is severity=error (audit OTA_FAILED is also error,
        # motion is "warning"); we want both error+, not exact match
        for a in result:
            assert a["severity"] in ("error", "critical")

    def test_before_filter(self, tmp_path):
        svc = self._two_camera_setup(tmp_path)
        # only events strictly older than the cutoff
        result = svc.list_alerts(
            user="alice",
            role="admin",
            before="2026-04-30T08:00:30Z",
        )
        # OTA_FAILED at 08:00:00 is before; motion at 08:01:00 is not
        assert any(a["source"] == "audit" for a in result)
        assert not any(a["source"] == "motion" for a in result)

    def test_limit(self, tmp_path):
        svc = self._two_camera_setup(tmp_path)
        result = svc.list_alerts(user="alice", role="admin", limit=1)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Read state
# ---------------------------------------------------------------------------


class TestReadState:
    def _basic(self, tmp_path):
        motion_events = [
            _FakeMotionEvent(
                id="mot-1",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:00:00Z",
                ended_at="2026-04-30T08:00:02Z",
                peak_score=0.18,
            ),
            _FakeMotionEvent(
                id="mot-2",
                camera_id="cam-d8ee",
                started_at="2026-04-30T08:01:00Z",
                ended_at="2026-04-30T08:01:02Z",
                peak_score=0.18,
            ),
        ]
        return _make_service(tmp_path, motion_events=motion_events)

    def test_unread_count_starts_at_total(self, tmp_path):
        svc = self._basic(tmp_path)
        assert svc.unread_count(user="alice", role="admin") == 2

    def test_mark_read_drops_unread_count(self, tmp_path):
        svc = self._basic(tmp_path)
        ok = svc.mark_read(user="alice", alert_id="motion:mot-1")
        assert ok
        assert svc.unread_count(user="alice", role="admin") == 1

    def test_per_user_isolation(self, tmp_path):
        svc = self._basic(tmp_path)
        svc.mark_read(user="alice", alert_id="motion:mot-1")
        # bob hasn't read anything
        assert svc.unread_count(user="bob", role="admin") == 2
        assert svc.unread_count(user="alice", role="admin") == 1

    def test_mark_read_idempotent(self, tmp_path):
        svc = self._basic(tmp_path)
        svc.mark_read(user="alice", alert_id="motion:mot-1")
        svc.mark_read(user="alice", alert_id="motion:mot-1")
        assert svc.unread_count(user="alice", role="admin") == 1

    def test_mark_read_rejects_garbage_id(self, tmp_path):
        svc = self._basic(tmp_path)
        assert svc.mark_read(user="alice", alert_id="totally-bogus") is False

    def test_mark_all_read_respects_filters(self, tmp_path):
        # mot-1 at 08:00:00, mot-2 at 08:01:00
        svc = self._basic(tmp_path)
        marked = svc.mark_all_read(
            user="alice", role="admin", before="2026-04-30T08:00:30Z"
        )
        assert marked == 1
        # mot-1 read; mot-2 still unread
        result = svc.list_alerts(user="alice", role="admin")
        by_id = {a["id"]: a for a in result}
        assert by_id["motion:mot-1"]["is_read"] is True
        assert by_id["motion:mot-2"]["is_read"] is False

    def test_mark_all_read_returns_zero_when_already_read(self, tmp_path):
        svc = self._basic(tmp_path)
        svc.mark_all_read(user="alice", role="admin")
        # second call: nothing left to flip
        marked = svc.mark_all_read(user="alice", role="admin")
        assert marked == 0


class TestPersistence:
    def test_state_survives_service_restart(self, tmp_path):
        cameras = [
            _FakeCamera(
                id="cam-d8ee",
                hardware_faults=[{"code": "boom", "severity": "error", "message": "x"}],
            )
        ]
        svc1 = _make_service(tmp_path, cameras=cameras)
        svc1.mark_read(user="alice", alert_id="fault:cam-d8ee:boom")
        # Spawn a new service with the same path → should pick up the read state
        svc2 = _make_service(tmp_path, cameras=cameras)
        assert svc2.unread_count(user="alice", role="admin") == 0

    def test_corrupt_file_falls_back_to_empty(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        bad = config_dir / "alert_read_state.json"
        bad.write_text("this is not json {{{ broken")
        # Should not raise; state starts empty.
        svc = _make_service(tmp_path)
        assert svc.unread_count(user="alice", role="admin") == 0

    def test_persisted_file_has_schema_version(self, tmp_path):
        cameras = [
            _FakeCamera(
                id="cam-d8ee",
                hardware_faults=[{"code": "boom", "severity": "error", "message": "x"}],
            )
        ]
        svc = _make_service(tmp_path, cameras=cameras)
        svc.mark_read(user="alice", alert_id="fault:cam-d8ee:boom")
        path = tmp_path / "config" / "alert_read_state.json"
        payload = json.loads(path.read_text())
        assert payload["schema_version"] == 1
        assert "alice" in payload["users"]


class TestUserDeletion:
    def test_forget_user_drops_their_state(self, tmp_path):
        cameras = [
            _FakeCamera(
                id="cam-d8ee",
                hardware_faults=[{"code": "x", "severity": "error", "message": "x"}],
            )
        ]
        svc = _make_service(tmp_path, cameras=cameras)
        svc.mark_read(user="alice", alert_id="fault:cam-d8ee:x")
        svc.mark_read(user="bob", alert_id="fault:cam-d8ee:x")

        assert svc.forget_user("alice") is True
        # alice is unread again; bob is still read
        assert svc.unread_count(user="alice", role="admin") == 1
        assert svc.unread_count(user="bob", role="admin") == 0

    def test_forget_unknown_user_is_no_op(self, tmp_path):
        svc = _make_service(tmp_path)
        assert svc.forget_user("nobody") is False
