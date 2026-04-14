"""Tests for the recordings service (orchestration layer)."""

from unittest.mock import MagicMock

import pytest

from monitor.models import Camera, Clip
from monitor.services.recordings_service import RecordingsService


def _make_clip(cam_id="cam-001", date="2026-04-09", time="14-30-00", size=1024):
    """Helper: create a fake clip file on disk and return path."""
    return Clip(
        camera_id=cam_id,
        filename=f"{time}.mp4",
        date=date,
        start_time=time.replace("-", ":"),
        size_bytes=size,
        thumbnail="",
    )


def _make_camera(cam_id="cam-001"):
    return Camera(
        id=cam_id,
        name="Test Cam",
        status="online",
    )


@pytest.fixture
def store():
    s = MagicMock()
    s.get_camera.return_value = _make_camera()
    return s


@pytest.fixture
def storage_manager(tmp_path):
    sm = MagicMock()
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    sm.recordings_dir = str(rec_dir)
    return sm


@pytest.fixture
def audit():
    return MagicMock()


@pytest.fixture
def svc(storage_manager, store, audit, tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    return RecordingsService(
        storage_manager=storage_manager,
        store=store,
        audit=audit,
        live_dir=str(live_dir),
        default_recordings_dir=str(tmp_path / "recordings"),
    )


def _create_clip_file(storage_manager, cam_id, date, time_str, size=1024):
    """Create a real clip file for integration-style tests."""
    from pathlib import Path

    clip_dir = Path(storage_manager.recordings_dir) / cam_id / date
    clip_dir.mkdir(parents=True, exist_ok=True)
    mp4 = clip_dir / f"{time_str}.mp4"
    mp4.write_bytes(b"x" * size)
    return mp4


class TestListClips:
    """Test list_clips delegation and validation."""

    def test_camera_not_found(self, svc, store):
        store.get_camera.return_value = None
        result, error, status = svc.list_clips("no-cam", "2026-04-09")
        assert result is None
        assert error == "Camera not found"
        assert status == 404

    def test_returns_clips(self, svc, storage_manager):
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-30-00")
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "15-00-00")
        result, error, status = svc.list_clips("cam-001", "2026-04-09")
        assert error is None
        assert status == 200
        assert len(result) == 2

    def test_empty_date(self, svc):
        result, error, status = svc.list_clips("cam-001", "2099-01-01")
        assert error is None
        assert result == []


class TestListDates:
    """Test list_dates delegation."""

    def test_camera_not_found(self, svc, store):
        store.get_camera.return_value = None
        result, error, status = svc.list_dates("no-cam")
        assert status == 404

    def test_returns_dates(self, svc, storage_manager):
        _create_clip_file(storage_manager, "cam-001", "2026-04-07", "10-00-00")
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-00-00")
        result, error, status = svc.list_dates("cam-001")
        assert error is None
        assert result["dates"] == ["2026-04-07", "2026-04-09"]


class TestLatestClip:
    """Test latest_clip delegation."""

    def test_camera_not_found(self, svc, store):
        store.get_camera.return_value = None
        result, error, status = svc.latest_clip("no-cam")
        assert status == 404

    def test_no_recordings(self, svc):
        result, error, status = svc.latest_clip("cam-001")
        assert error == "No recordings found"
        assert status == 404

    def test_returns_latest(self, svc, storage_manager):
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-00-00")
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "15-30-00")
        result, error, status = svc.latest_clip("cam-001")
        assert error is None
        assert result["start_time"] == "15:30:00"


class TestResolveClipPath:
    """Test resolve_clip_path."""

    def test_invalid_filename(self, svc):
        result, error, status = svc.resolve_clip_path(
            "cam-001", "2026-04-09", "bad.txt"
        )
        assert error == "Invalid filename"
        assert status == 400

    def test_not_found(self, svc):
        result, error, status = svc.resolve_clip_path(
            "cam-001", "2026-04-09", "99-99-99.mp4"
        )
        assert error == "Clip not found"
        assert status == 404

    def test_found(self, svc, storage_manager):
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-30-00")
        result, error, status = svc.resolve_clip_path(
            "cam-001", "2026-04-09", "14-30-00.mp4"
        )
        assert error is None
        assert result.name == "14-30-00.mp4"


class TestDeleteClip:
    """Test delete_clip with audit logging."""

    def test_invalid_filename(self, svc):
        result, error, status = svc.delete_clip("cam-001", "2026-04-09", "bad.avi")
        assert error == "Invalid filename"
        assert status == 400

    def test_not_found(self, svc):
        result, error, status = svc.delete_clip("cam-001", "2026-04-09", "99-99-99.mp4")
        assert error == "Clip not found"
        assert status == 404

    def test_deletes_and_audits(self, svc, storage_manager, audit):
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-30-00")
        result, error, status = svc.delete_clip(
            "cam-001",
            "2026-04-09",
            "14-30-00.mp4",
            requesting_user="admin",
            requesting_ip="127.0.0.1",
        )
        assert error is None
        assert status == 200
        assert result["message"] == "Clip deleted"
        audit.log_event.assert_called_once()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "CLIP_DELETED"

    def test_no_audit_logger(self, storage_manager, store, tmp_path):
        """Service works without audit logger."""
        svc = RecordingsService(
            storage_manager=storage_manager,
            store=store,
            audit=None,
            live_dir=str(tmp_path / "live"),
        )
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-30-00")
        result, error, status = svc.delete_clip("cam-001", "2026-04-09", "14-30-00.mp4")
        assert error is None
        assert status == 200


class TestFallbackRecordingsDir:
    """Test fallback when storage_manager is None."""

    def test_uses_default_dir(self, store, tmp_path):
        rec_dir = tmp_path / "fallback"
        rec_dir.mkdir()
        svc = RecordingsService(
            storage_manager=None,
            store=store,
            default_recordings_dir=str(rec_dir),
            live_dir=str(tmp_path / "live"),
        )
        # Should not crash — uses default_recordings_dir
        result, error, status = svc.list_clips("cam-001", "2026-04-09")
        assert error is None
        assert result == []
