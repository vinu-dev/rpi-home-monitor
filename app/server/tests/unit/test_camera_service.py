"""Tests for the camera management service."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from monitor.services.camera_service import CameraService


def _make_camera(**overrides):
    """Create a fake camera object with sensible defaults."""
    defaults = {
        "id": "cam-001",
        "name": "Front Door",
        "location": "Porch",
        "status": "pending",
        "ip": "192.168.1.50",
        "recording_mode": "continuous",
        "resolution": "1080p",
        "fps": 15,
        "paired_at": "",
        "last_seen": "2026-04-11T10:00:00Z",
        "firmware_version": "1.0.0",
        "rtsp_url": "",
        "width": 1920,
        "height": 1080,
        "bitrate": 4000000,
        "h264_profile": "high",
        "keyframe_interval": 30,
        "rotation": 0,
        "hflip": False,
        "vflip": False,
        "config_sync": "unknown",
        # Heartbeat fields (ADR-0016)
        "streaming": False,
        "cpu_temp": 0.0,
        "memory_percent": 0,
        "uptime_seconds": 0,
        "pairing_secret": "",
        # ADR-0017 recording-mode + on-demand streaming fields
        "recording_schedule": [],
        "recording_motion_enabled": False,
        "desired_stream_state": "stopped",
        "motion_sensitivity": 5,
        "image_controls": {},
        "image_quality": {},
        # #136 offline alerts
        "offline_alerts_enabled": True,
        "last_offline_alert_at": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestListCameras:
    """Test listing all cameras."""

    def test_returns_empty_list_when_no_cameras(self):
        store = MagicMock()
        store.get_cameras.return_value = []
        svc = CameraService(store)
        assert svc.list_cameras() == []

    def test_returns_serialized_camera_dicts(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_cameras.return_value = [cam]
        svc = CameraService(store)
        result = svc.list_cameras()
        assert len(result) == 1
        assert result[0]["id"] == "cam-001"
        assert result[0]["name"] == "Front Door"
        assert result[0]["location"] == "Porch"
        assert result[0]["status"] == "pending"
        assert result[0]["ip"] == "192.168.1.50"
        assert result[0]["recording_mode"] == "continuous"
        assert result[0]["resolution"] == "1080p"
        assert result[0]["fps"] == 15
        assert result[0]["paired_at"] == ""
        assert result[0]["last_seen"] == "2026-04-11T10:00:00Z"
        assert result[0]["firmware_version"] == "1.0.0"

    def test_returns_multiple_cameras(self):
        store = MagicMock()
        store.get_cameras.return_value = [
            _make_camera(id="cam-001"),
            _make_camera(id="cam-002", name="Back Yard"),
        ]
        svc = CameraService(store)
        result = svc.list_cameras()
        assert len(result) == 2
        assert result[0]["id"] == "cam-001"
        assert result[1]["id"] == "cam-002"

    def test_does_not_include_rtsp_url(self):
        store = MagicMock()
        store.get_cameras.return_value = [_make_camera(rtsp_url="rtsp://x")]
        svc = CameraService(store)
        result = svc.list_cameras()
        assert "rtsp_url" not in result[0]


class TestAddCamera:
    """Test registering a new pending camera."""

    def test_creates_pending_camera(self):
        store = MagicMock()
        store.get_camera.return_value = None
        svc = CameraService(store)
        result, error, status = svc.add_camera("cam-new", "Front Door", "Outdoor")
        assert status == 201
        assert error == ""
        assert result["id"] == "cam-new"
        assert result["name"] == "Front Door"
        assert result["status"] == "pending"
        store.save_camera.assert_called_once()
        saved = store.save_camera.call_args[0][0]
        assert saved.id == "cam-new"
        assert saved.location == "Outdoor"

    def test_rejects_empty_id(self):
        store = MagicMock()
        svc = CameraService(store)
        result, error, status = svc.add_camera("", "Name", "Loc")
        assert status == 400
        assert "required" in error.lower()
        store.save_camera.assert_not_called()

    def test_rejects_duplicate(self):
        store = MagicMock()
        store.get_camera.return_value = _make_camera(id="cam-dup")
        svc = CameraService(store)
        result, error, status = svc.add_camera("cam-dup")
        assert status == 409
        assert "exists" in error.lower()
        store.save_camera.assert_not_called()

    def test_defaults_name_to_id(self):
        store = MagicMock()
        store.get_camera.return_value = None
        svc = CameraService(store)
        result, error, status = svc.add_camera("cam-xyz")
        assert status == 201
        assert result["name"] == "cam-xyz"

    def test_strips_whitespace(self):
        store = MagicMock()
        store.get_camera.return_value = None
        svc = CameraService(store)
        result, error, status = svc.add_camera("  cam-ws  ", "  My Cam  ", "  Yard  ")
        assert status == 201
        saved = store.save_camera.call_args[0][0]
        assert saved.id == "cam-ws"
        assert saved.name == "My Cam"
        assert saved.location == "Yard"


class TestGetCameraStatus:
    """Test getting camera status."""

    def test_returns_status_dict_for_existing_camera(self):
        cam = _make_camera(status="online")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        result, error = svc.get_camera_status("cam-001")
        assert error == ""
        assert result["id"] == "cam-001"
        assert result["name"] == "Front Door"
        assert result["status"] == "online"
        assert result["ip"] == "192.168.1.50"
        assert result["last_seen"] == "2026-04-11T10:00:00Z"
        assert result["firmware_version"] == "1.0.0"
        assert result["resolution"] == "1080p"
        assert result["fps"] == 15
        assert result["recording_mode"] == "continuous"

    def test_returns_error_when_camera_not_found(self):
        store = MagicMock()
        store.get_camera.return_value = None
        svc = CameraService(store)
        result, error = svc.get_camera_status("nonexistent")
        assert result is None
        assert error == "Camera not found"


class TestConfirm:
    """Test confirming a pending camera."""

    def test_confirms_pending_camera(self):
        cam = _make_camera(status="pending")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        result, error, status = svc.confirm("cam-001", name="My Cam")
        assert status == 200
        assert error == ""
        assert result["id"] == "cam-001"
        assert result["name"] == "My Cam"
        assert result["status"] == "online"
        assert result["paired_at"] != ""
        assert cam.status == "online"
        assert cam.rtsp_url == "rtsp://127.0.0.1:8554/cam-001"
        store.save_camera.assert_called_once_with(cam)

    def test_uses_existing_name_when_none_given(self):
        cam = _make_camera(status="pending", name="Existing Name")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        result, _, _ = svc.confirm("cam-001")
        assert result["name"] == "Existing Name"

    def test_uses_camera_id_as_fallback_name(self):
        cam = _make_camera(status="pending", name="")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        result, _, _ = svc.confirm("cam-001")
        assert result["name"] == "cam-001"

    def test_sets_location_when_provided(self):
        cam = _make_camera(status="pending", location="")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.confirm("cam-001", location="Kitchen")
        assert cam.location == "Kitchen"

    def test_rejects_already_confirmed_camera(self):
        cam = _make_camera(status="online")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        result, error, status = svc.confirm("cam-001")
        assert status == 200
        assert error == ""
        assert result["status"] == "online"
        store.save_camera.assert_not_called()

    def test_confirm_is_idempotent_for_offline_camera(self):
        cam = _make_camera(status="offline", paired_at="2026-04-11T10:00:00Z")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        result, error, status = svc.confirm("cam-001")
        assert status == 200
        assert error == ""
        assert result["status"] == "offline"
        assert result["paired_at"] == "2026-04-11T10:00:00Z"
        store.save_camera.assert_not_called()

    def test_returns_404_when_camera_not_found(self):
        store = MagicMock()
        store.get_camera.return_value = None
        svc = CameraService(store)
        result, error, status = svc.confirm("nonexistent")
        assert status == 404
        assert error == "Camera not found"
        assert result is None

    def test_starts_streaming_when_continuous_mode(self):
        cam = _make_camera(status="pending", recording_mode="continuous")
        store = MagicMock()
        store.get_camera.return_value = cam
        streaming = MagicMock()
        svc = CameraService(store, streaming=streaming)
        svc.confirm("cam-001")
        streaming.start_camera.assert_called_once_with("cam-001")

    def test_does_not_start_streaming_when_off_mode(self):
        cam = _make_camera(status="pending", recording_mode="off")
        store = MagicMock()
        store.get_camera.return_value = cam
        streaming = MagicMock()
        svc = CameraService(store, streaming=streaming)
        svc.confirm("cam-001")
        streaming.start_camera.assert_not_called()

    def test_works_without_streaming_service(self):
        cam = _make_camera(status="pending", recording_mode="continuous")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store, streaming=None)
        result, error, status = svc.confirm("cam-001")
        assert status == 200


class TestUpdate:
    """Test updating camera settings."""

    def test_updates_name(self):
        cam = _make_camera(status="online")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"name": "New Name"})
        assert status == 200
        assert error == ""
        assert cam.name == "New Name"
        store.save_camera.assert_called_once_with(cam)

    def test_updates_offline_alerts_enabled(self):
        """#136 — operator can mute offline alerts for a known-flaky
        camera via Camera Settings. Default is True; we toggle to
        False and confirm the field persists.
        """
        cam = _make_camera(status="online", offline_alerts_enabled=True)
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"offline_alerts_enabled": False})
        assert status == 200, error
        assert cam.offline_alerts_enabled is False

    def test_offline_alerts_enabled_must_be_bool(self):
        """Defensive — an API client sending {"offline_alerts_enabled":
        "true"} (string) gets rejected, not silently coerced. The
        downstream gate uses ``not enabled`` so any truthy non-bool
        would silently change behaviour.
        """
        cam = _make_camera(status="online")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"offline_alerts_enabled": "false"})
        assert status == 400
        assert "boolean" in error.lower()

    def test_returns_404_when_camera_not_found(self):
        store = MagicMock()
        store.get_camera.return_value = None
        svc = CameraService(store)
        error, status = svc.update("nonexistent", {"name": "X"})
        assert status == 404
        assert error == "Camera not found"

    def test_rejects_empty_data(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {})
        assert status == 400
        assert error == "JSON body required"

    def test_rejects_unknown_fields(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"unknown_field": "value"})
        assert status == 400
        assert "Unknown fields" in error

    def test_rejects_invalid_recording_mode(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        # ADR-0017: 'motion' is now an accepted (forward-compat) mode.
        # An unknown string must still be rejected.
        error, status = svc.update("cam-001", {"recording_mode": "bogus"})
        assert status == 400
        assert "recording_mode" in error

    def test_rejects_invalid_resolution(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"resolution": "4k"})
        assert status == 400
        assert "resolution" in error

    def test_rejects_fps_out_of_range(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"fps": 0})
        assert status == 400
        assert "fps" in error

    def test_rejects_fps_above_max(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"fps": 31})
        assert status == 400
        assert "fps" in error

    def test_rejects_non_integer_fps(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"fps": 15.5})
        assert status == 400
        assert "fps" in error

    def test_rejects_name_too_long(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"name": "x" * 65})
        assert status == 400
        assert "name" in error

    def test_rejects_empty_name(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.update("cam-001", {"name": ""})
        assert status == 400
        assert "name" in error

    def test_mode_change_off_to_continuous_no_direct_streaming_call(self):
        """ADR-0017: pipeline changes are driven by RecordingScheduler, not here."""
        cam = _make_camera(recording_mode="off")
        store = MagicMock()
        store.get_camera.return_value = cam
        streaming = MagicMock()
        svc = CameraService(store, streaming=streaming)
        svc.update("cam-001", {"recording_mode": "continuous"})
        streaming.start_camera.assert_not_called()
        streaming.stop_camera.assert_not_called()

    def test_mode_change_continuous_to_off_no_direct_streaming_call(self):
        cam = _make_camera(recording_mode="continuous")
        store = MagicMock()
        store.get_camera.return_value = cam
        streaming = MagicMock()
        svc = CameraService(store, streaming=streaming)
        svc.update("cam-001", {"recording_mode": "off"})
        streaming.start_camera.assert_not_called()
        streaming.stop_camera.assert_not_called()

    def test_no_streaming_change_when_mode_unchanged(self):
        cam = _make_camera(recording_mode="continuous")
        store = MagicMock()
        store.get_camera.return_value = cam
        streaming = MagicMock()
        svc = CameraService(store, streaming=streaming)
        svc.update("cam-001", {"name": "New Name"})
        streaming.start_camera.assert_not_called()
        streaming.stop_camera.assert_not_called()


class TestDelete:
    """Test deleting a camera."""

    def test_deletes_existing_camera(self):
        store = MagicMock()
        store.delete_camera.return_value = True
        svc = CameraService(store)
        error, status = svc.delete("cam-001")
        assert status == 200
        assert error == ""
        store.delete_camera.assert_called_once_with("cam-001")

    def test_returns_404_when_camera_not_found(self):
        store = MagicMock()
        store.delete_camera.return_value = False
        svc = CameraService(store)
        error, status = svc.delete("nonexistent")
        assert status == 404
        assert error == "Camera not found"

    def test_stops_streaming_before_delete(self):
        store = MagicMock()
        store.delete_camera.return_value = True
        streaming = MagicMock()
        svc = CameraService(store, streaming=streaming)
        svc.delete("cam-001")
        streaming.stop_camera.assert_called_once_with("cam-001")

    def test_works_without_streaming_service(self):
        store = MagicMock()
        store.delete_camera.return_value = True
        svc = CameraService(store, streaming=None)
        error, status = svc.delete("cam-001")
        assert status == 200


class TestAuditLogging:
    """Test audit logging across all mutating operations."""

    def test_confirm_logs_audit_event(self):
        cam = _make_camera(status="pending")
        store = MagicMock()
        store.get_camera.return_value = cam
        audit = MagicMock()
        svc = CameraService(store, audit=audit)
        svc.confirm("cam-001", user="admin", ip="10.0.0.1")
        audit.log_event.assert_called_once()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "CAMERA_CONFIRMED"
        assert call_args[1]["user"] == "admin"
        assert call_args[1]["ip"] == "10.0.0.1"

    def test_update_logs_audit_event(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        audit = MagicMock()
        svc = CameraService(store, audit=audit)
        svc.update("cam-001", {"name": "New"}, user="admin", ip="10.0.0.1")
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "CAMERA_UPDATED"

    def test_delete_logs_audit_event(self):
        store = MagicMock()
        store.delete_camera.return_value = True
        audit = MagicMock()
        svc = CameraService(store, audit=audit)
        svc.delete("cam-001", user="admin", ip="10.0.0.1")
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "CAMERA_DELETED"

    def test_audit_failure_does_not_break_confirm(self):
        cam = _make_camera(status="pending")
        store = MagicMock()
        store.get_camera.return_value = cam
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("disk full")
        svc = CameraService(store, audit=audit)
        result, error, status = svc.confirm("cam-001")
        assert status == 200

    def test_audit_failure_does_not_break_update(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("disk full")
        svc = CameraService(store, audit=audit)
        error, status = svc.update("cam-001", {"name": "X"})
        assert status == 200

    def test_audit_failure_does_not_break_delete(self):
        store = MagicMock()
        store.delete_camera.return_value = True
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("disk full")
        svc = CameraService(store, audit=audit)
        error, status = svc.delete("cam-001")
        assert status == 200

    def test_no_audit_when_audit_service_is_none(self):
        cam = _make_camera(status="pending")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store, audit=None)
        result, error, status = svc.confirm("cam-001")
        assert status == 200


class TestAcceptCameraConfig:
    """Test accept_camera_config (camera-initiated config push)."""

    def test_updates_stream_params(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.accept_camera_config(
            "cam-001", {"width": 640, "height": 480, "fps": 30}
        )
        assert status == 200
        assert error == ""
        assert cam.width == 640
        assert cam.height == 480
        assert cam.fps == 30
        assert cam.config_sync == "synced"
        store.save_camera.assert_called_once_with(cam)

    def test_rejects_unknown_camera(self):
        store = MagicMock()
        store.get_camera.return_value = None
        svc = CameraService(store)
        error, status = svc.accept_camera_config("cam-nope", {"fps": 15})
        assert status == 404
        assert "not found" in error.lower()

    def test_rejects_unknown_param(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        error, status = svc.accept_camera_config("cam-001", {"unknown": 42})
        assert status == 400
        assert "Unknown" in error

    def test_does_not_push_back_to_camera(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        control = MagicMock()
        svc = CameraService(store, control_client=control)
        svc.accept_camera_config("cam-001", {"fps": 15})
        control.set_config.assert_not_called()

    def test_logs_audit_event(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        audit = MagicMock()
        svc = CameraService(store, audit=audit)
        svc.accept_camera_config("cam-001", {"fps": 15})
        audit.log_event.assert_called_once()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "CAMERA_CONFIG_RECEIVED"


class TestAcceptHeartbeat:
    """Tests for CameraService.accept_heartbeat() (ADR-0016)."""

    def _basic_payload(self, **overrides):
        data = {
            "streaming": True,
            "cpu_temp": 48.5,
            "memory_percent": 42,
            "uptime_seconds": 3600,
            "stream_config": {
                "width": 1920,
                "height": 1080,
                "fps": 25,
                "bitrate": 4000000,
                "h264_profile": "high",
                "keyframe_interval": 30,
                "rotation": 0,
                "hflip": False,
                "vflip": False,
            },
        }
        data.update(overrides)
        return data

    def test_returns_404_for_unknown_camera(self):
        store = MagicMock()
        store.get_camera.return_value = None
        svc = CameraService(store)
        _, error, code = svc.accept_heartbeat("cam-999", self._basic_payload())
        assert code == 404
        assert error

    def test_marks_camera_online(self):
        cam = _make_camera(status="offline")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat("cam-001", self._basic_payload())
        assert cam.status == "online"

    def test_updates_last_seen(self):
        cam = _make_camera(last_seen="2020-01-01T00:00:00Z")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat("cam-001", self._basic_payload())
        assert cam.last_seen != "2020-01-01T00:00:00Z"
        assert "Z" in cam.last_seen

    def test_updates_streaming_flag(self):
        cam = _make_camera(streaming=False)
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat("cam-001", self._basic_payload(streaming=True))
        assert cam.streaming is True

    def test_updates_streaming_false(self):
        cam = _make_camera(streaming=True)
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat("cam-001", self._basic_payload(streaming=False))
        assert cam.streaming is False

    def test_accepts_hardware_fault_from_heartbeat(self):
        """Camera reports a hardware fault — server stores + surfaces it.

        Prevents the dashboard "no camera module detected" banner
        from silently regressing to always-green.
        """
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        payload = self._basic_payload(
            hardware_ok=False,
            hardware_error="No camera module detected.",
        )
        svc.accept_heartbeat("cam-001", payload)
        assert cam.hardware_ok is False
        assert cam.hardware_error == "No camera module detected."

    def test_clears_hardware_fault_when_recovered(self):
        """When the camera reports ok=true, the previously stored error is cleared.

        Covers the "operator plugged the ribbon cable back in" path.
        """
        cam = _make_camera()
        cam.hardware_ok = False
        cam.hardware_error = "No camera module detected."
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat(
            "cam-001",
            self._basic_payload(hardware_ok=True, hardware_error=""),
        )
        assert cam.hardware_ok is True
        assert cam.hardware_error == ""

    def test_ignores_malformed_hardware_error(self):
        """Non-string hardware_error is dropped silently."""
        cam = _make_camera()
        # Seed the real attribute name so we can assert it was left alone.
        cam.hardware_error = "prior-value"
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat(
            "cam-001",
            self._basic_payload(hardware_error={"evil": "dict"}),
        )
        # Malformed value is ignored — prior value preserved, no exception.
        assert cam.hardware_error == "prior-value"

    def test_accepts_structured_hardware_faults(self):
        """Structured hardware_faults list is stored on the Camera (ADR-0023)."""
        cam = _make_camera()
        cam.hardware_faults = []
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        payload = self._basic_payload(
            hardware_faults=[
                {
                    "code": "camera_sensor_missing",
                    "severity": "error",
                    "message": "Camera sensor missing",
                    "hint": "Check ribbon cable and dtoverlay.",
                    "context": {"device": "/dev/video14"},
                }
            ],
        )
        svc.accept_heartbeat("cam-001", payload)
        assert len(cam.hardware_faults) == 1
        f = cam.hardware_faults[0]
        assert f["code"] == "camera_sensor_missing"
        assert f["severity"] == "error"
        assert f["message"] == "Camera sensor missing"
        assert f["hint"] == "Check ribbon cable and dtoverlay."
        assert f["context"] == {"device": "/dev/video14"}

    def test_hardware_faults_drops_malformed_entries(self):
        """Entries missing ``code`` or not dicts are skipped; valid ones kept."""
        cam = _make_camera()
        cam.hardware_faults = []
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        payload = self._basic_payload(
            hardware_faults=[
                "not a dict",  # skipped
                {"severity": "error"},  # missing code → skipped
                {"code": "camera_sensor_missing"},  # kept, defaults filled
                {"code": "", "severity": "error"},  # empty code → skipped
            ],
        )
        svc.accept_heartbeat("cam-001", payload)
        assert len(cam.hardware_faults) == 1
        assert cam.hardware_faults[0]["code"] == "camera_sensor_missing"

    def test_recording_motion_enabled_translates_to_motion_detection_on_wire(self):
        """When the admin toggles motion on, the server must push ``motion_detection``
        to the camera — not ``recording_motion_enabled`` (the server-side model
        field name). The camera config reads ``MOTION_DETECTION``; using the
        server-side name leaves motion detection off silently.

        Regression for the bug discovered on 2026-04-22: Front Door
        showed motion_enabled=true on the dashboard but was actually
        running with motion off — no events ever fired.
        """
        cam = _make_camera(
            id="cam-001", ip="192.168.1.148", recording_motion_enabled=False
        )
        cam.hardware_faults = []
        store = MagicMock()
        store.get_camera.return_value = cam
        control = MagicMock()
        control.set_config.return_value = ({"applied": {}}, "")
        svc = CameraService(store, control_client=control)
        svc.update("cam-001", {"recording_motion_enabled": True})
        # The key pushed to the control client must be motion_detection.
        control.set_config.assert_called_once()
        pushed = control.set_config.call_args[0][1]
        assert "motion_detection" in pushed
        assert pushed["motion_detection"] is True
        assert "recording_motion_enabled" not in pushed

    def test_hardware_faults_caps_list_length(self):
        """Runaway camera can't bloat the store with thousands of faults."""
        cam = _make_camera()
        cam.hardware_faults = []
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        payload = self._basic_payload(
            hardware_faults=[{"code": f"fault_{i}"} for i in range(128)],
        )
        svc.accept_heartbeat("cam-001", payload)
        # Cap is 32 (see CameraService.accept_heartbeat).
        assert len(cam.hardware_faults) == 32

    def test_updates_health_metrics(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat(
            "cam-001",
            self._basic_payload(cpu_temp=55.2, memory_percent=60, uptime_seconds=7200),
        )
        assert cam.cpu_temp == 55.2
        assert cam.memory_percent == 60
        assert cam.uptime_seconds == 7200

    def test_accepts_stream_config_from_heartbeat(self):
        cam = _make_camera(fps=25, config_sync="unknown")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        payload = self._basic_payload()
        payload["stream_config"]["fps"] = 15
        svc.accept_heartbeat("cam-001", payload)
        assert cam.fps == 15
        assert cam.config_sync == "synced"

    def test_returns_pending_config_when_config_sync_pending(self):
        cam = _make_camera(config_sync="pending", fps=30)
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        response, _, code = svc.accept_heartbeat("cam-001", self._basic_payload())
        assert code == 200
        assert "pending_config" in response
        assert response["pending_config"]["fps"] == 30

    def test_pending_config_replays_all_camera_control_fields(self):
        """Offline updates must replay the same fields direct pushes send."""
        cam = _make_camera(
            config_sync="pending",
            motion_sensitivity=8,
            recording_motion_enabled=True,
            image_quality={"Sharpness": 1.5},
        )
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        response, _, code = svc.accept_heartbeat("cam-001", self._basic_payload())
        assert code == 200
        pending = response["pending_config"]
        assert pending["motion_sensitivity"] == 8
        assert pending["motion_detection"] is True
        assert pending["image_quality"] == {"Sharpness": 1.5}
        assert "recording_motion_enabled" not in pending

    def test_no_pending_config_when_synced(self):
        cam = _make_camera(config_sync="synced")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        response, _, code = svc.accept_heartbeat("cam-001", self._basic_payload())
        assert code == 200
        assert "pending_config" not in response

    def test_logs_camera_online_audit_when_was_offline(self):
        cam = _make_camera(status="offline")
        store = MagicMock()
        store.get_camera.return_value = cam
        audit = MagicMock()
        svc = CameraService(store, audit=audit)
        svc.accept_heartbeat("cam-001", self._basic_payload())
        audit.log_event.assert_called_once()
        event = audit.log_event.call_args[0][0]
        assert event == "CAMERA_ONLINE"

    def test_no_audit_when_already_online(self):
        cam = _make_camera(status="online")
        store = MagicMock()
        store.get_camera.return_value = cam
        audit = MagicMock()
        svc = CameraService(store, audit=audit)
        svc.accept_heartbeat("cam-001", self._basic_payload())
        audit.log_event.assert_not_called()

    def test_saves_camera_to_store(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat("cam-001", self._basic_payload())
        store.save_camera.assert_called_once_with(cam)

    def test_ignores_invalid_cpu_temp(self):
        cam = _make_camera(cpu_temp=0.0)
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat("cam-001", self._basic_payload(cpu_temp="bad"))
        # Should not crash; cpu_temp stays unchanged
        assert cam.cpu_temp == 0.0

    def test_heartbeat_without_stream_config_is_ok(self):
        cam = _make_camera()
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        payload = {
            "streaming": False,
            "cpu_temp": 40.0,
            "memory_percent": 30,
            "uptime_seconds": 100,
        }
        _, error, code = svc.accept_heartbeat("cam-001", payload)
        assert code == 200
        assert not error


# --- Sensor capabilities (#173) ---


class TestSensorCapabilitiesIngestion:
    """The camera-side heartbeat embeds a ``capabilities`` block carrying
    sensor identity + supported modes (#173). The service persists the
    block on the Camera record so the dashboard can render per-camera
    Settings dropdowns and so future updates can be validated against
    the live sensor's actual mode list."""

    def _capabilities_payload(self, **overrides):
        return {
            "streaming": False,
            "cpu_temp": 40.0,
            "memory_percent": 30,
            "uptime_seconds": 100,
            "capabilities": {
                "sensor_model": "imx219",
                "sensor_modes": [
                    {"width": 640, "height": 480, "max_fps": 58.0},
                    {"width": 1640, "height": 1232, "max_fps": 41.0},
                    {"width": 1920, "height": 1080, "max_fps": 47.0},
                    {"width": 3280, "height": 2464, "max_fps": 21.0},
                ],
                "detection_method": "picamera2",
                **overrides,
            },
        }

    def test_persists_imx219_capabilities(self):
        cam = _make_camera(sensor_model="", sensor_modes=[])
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat("cam-001", self._capabilities_payload())
        assert cam.sensor_model == "imx219"
        assert {(m["width"], m["height"]) for m in cam.sensor_modes} == {
            (640, 480),
            (1640, 1232),
            (1920, 1080),
            (3280, 2464),
        }
        assert cam.sensor_detection_method == "picamera2"

    def test_lowercases_and_trims_sensor_model(self):
        cam = _make_camera(sensor_model="")
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat(
            "cam-001",
            self._capabilities_payload(sensor_model="  IMX219  "),
        )
        assert cam.sensor_model == "imx219"

    def test_drops_garbage_modes(self):
        cam = _make_camera(sensor_model="", sensor_modes=[])
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat(
            "cam-001",
            self._capabilities_payload(
                sensor_modes=[
                    {"width": 1920, "height": 1080, "max_fps": 30.0},  # ok
                    {"width": "bad", "height": 1080, "max_fps": 30.0},  # type
                    {"width": -1, "height": 1080, "max_fps": 30.0},  # neg
                    {"height": 1080, "max_fps": 30.0},  # missing width
                    {"width": 1920, "height": 1080, "max_fps": 9999.0},  # absurd
                    "not a dict",
                ]
            ),
        )
        # Only the one well-formed entry survives.
        assert len(cam.sensor_modes) == 1
        assert cam.sensor_modes[0]["width"] == 1920

    def test_caps_persisted_modes_at_max(self):
        cam = _make_camera(sensor_model="", sensor_modes=[])
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        # Hand-craft a payload with way more modes than we accept.
        from monitor.services.camera_service import MAX_SENSOR_MODES

        modes = [
            {"width": 640 + i, "height": 480, "max_fps": 30.0}
            for i in range(MAX_SENSOR_MODES + 5)
        ]
        svc.accept_heartbeat(
            "cam-001",
            self._capabilities_payload(sensor_modes=modes),
        )
        assert len(cam.sensor_modes) == MAX_SENSOR_MODES

    def test_unknown_capabilities_block_is_ignored(self):
        """Garbage shape under ``capabilities`` does not crash or
        clobber the existing record."""
        cam = _make_camera(sensor_model="ov5647", sensor_modes=[{"a": 1}])
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat(
            "cam-001",
            {
                "streaming": False,
                "capabilities": "not-a-dict",
            },
        )
        assert cam.sensor_model == "ov5647"
        assert cam.sensor_modes == [{"a": 1}]

    def test_pre_173_payload_leaves_record_untouched(self):
        """Cameras on older firmware omit ``capabilities`` entirely.
        The existing sensor fields on the record must not be cleared."""
        cam = _make_camera(
            sensor_model="ov5647",
            sensor_modes=[{"width": 1920, "height": 1080, "max_fps": 30.0}],
        )
        store = MagicMock()
        store.get_camera.return_value = cam
        svc = CameraService(store)
        svc.accept_heartbeat(
            "cam-001",
            {
                "streaming": False,
                "cpu_temp": 40.0,
                "memory_percent": 30,
                "uptime_seconds": 100,
            },
        )
        assert cam.sensor_model == "ov5647"
        assert cam.sensor_modes == [{"width": 1920, "height": 1080, "max_fps": 30.0}]


class TestPerCameraValidation:
    """Update validation respects each camera's reported sensor modes
    when available, falling back to the legacy preset bounds for
    pre-#173 cameras."""

    def _service_with_camera(self, **overrides):
        cam = _make_camera(**overrides)
        store = MagicMock()
        store.get_camera.return_value = cam
        return CameraService(store), cam

    def test_imx219_camera_accepts_3280x2464(self):
        svc, _ = self._service_with_camera(
            sensor_model="imx219",
            sensor_modes=[
                {"width": 1920, "height": 1080, "max_fps": 47.0},
                {"width": 3280, "height": 2464, "max_fps": 21.0},
            ],
        )
        err, code = svc.update("cam-001", {"width": 3280, "height": 2464, "fps": 21})
        assert code == 200, err

    def test_ov5647_camera_rejects_imx219_resolution(self):
        svc, _ = self._service_with_camera(
            sensor_model="ov5647",
            sensor_modes=[
                {"width": 1920, "height": 1080, "max_fps": 30.0},
                {"width": 2592, "height": 1944, "max_fps": 15.0},
            ],
        )
        err, code = svc.update("cam-001", {"width": 3280, "height": 2464})
        assert code == 400
        assert "not supported by sensor" in err

    def test_imx219_camera_accepts_47fps_at_1080p(self):
        svc, _ = self._service_with_camera(
            sensor_model="imx219",
            sensor_modes=[
                {"width": 1920, "height": 1080, "max_fps": 47.0},
            ],
        )
        err, code = svc.update("cam-001", {"width": 1920, "height": 1080, "fps": 47})
        assert code == 200, err

    def test_ov5647_camera_rejects_fps_above_selected_mode_cap(self):
        svc, _ = self._service_with_camera(
            sensor_model="ov5647",
            sensor_modes=[
                {"width": 1920, "height": 1080, "max_fps": 30.0},
                {"width": 1296, "height": 972, "max_fps": 43.0},
            ],
        )
        err, code = svc.update("cam-001", {"width": 1920, "height": 1080, "fps": 43})
        assert code == 400
        assert "must be 1-30 for 1920x1080" in err

    def test_ov5647_camera_accepts_high_fps_on_matching_mode(self):
        svc, _ = self._service_with_camera(
            sensor_model="ov5647",
            sensor_modes=[
                {"width": 1920, "height": 1080, "max_fps": 30.0},
                {"width": 1296, "height": 972, "max_fps": 43.0},
            ],
        )
        err, code = svc.update("cam-001", {"width": 1296, "height": 972, "fps": 43})
        assert code == 200, err

    def test_pre_173_camera_keeps_legacy_30fps_cap(self):
        svc, _ = self._service_with_camera(sensor_model="", sensor_modes=[])
        err, code = svc.update("cam-001", {"fps": 47})
        assert code == 400
        assert "1 and 30" in err
