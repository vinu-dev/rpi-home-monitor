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
        assert status == 400
        assert error == "Camera is already confirmed"
        assert result is None
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
        error, status = svc.update("cam-001", {"recording_mode": "motion"})
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

    def test_starts_streaming_on_off_to_continuous_transition(self):
        cam = _make_camera(recording_mode="off")
        store = MagicMock()
        store.get_camera.return_value = cam
        streaming = MagicMock()
        svc = CameraService(store, streaming=streaming)
        svc.update("cam-001", {"recording_mode": "continuous"})
        streaming.start_camera.assert_called_once_with("cam-001")

    def test_stops_streaming_on_continuous_to_off_transition(self):
        cam = _make_camera(recording_mode="continuous")
        store = MagicMock()
        store.get_camera.return_value = cam
        streaming = MagicMock()
        svc = CameraService(store, streaming=streaming)
        svc.update("cam-001", {"recording_mode": "off"})
        streaming.stop_camera.assert_called_once_with("cam-001")

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
