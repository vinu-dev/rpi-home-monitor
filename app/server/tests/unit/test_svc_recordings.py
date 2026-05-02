# REQ: SWR-029; RISK: RISK-014; SEC: SC-014; TEST: TC-026
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


class TestListCameraSources:
    """Test the /recordings/cameras aggregator (paired + orphans)."""

    def test_paired_online_and_offline(self, svc, store):
        store.get_cameras.return_value = [
            Camera(id="cam-a", name="Front", status="online"),
            Camera(id="cam-b", name="Back", status="offline"),
        ]
        result, error, status = svc.list_camera_sources()
        assert status == 200 and error is None
        ids = [c["id"] for c in result]
        assert ids == ["cam-a", "cam-b"]
        assert [c["status"] for c in result] == ["online", "offline"]

    def test_pending_cameras_are_excluded(self, svc, store):
        store.get_cameras.return_value = [
            Camera(id="cam-a", name="Front", status="online"),
            Camera(id="cam-p", name="NewOne", status="pending"),
        ]
        result, _, _ = svc.list_camera_sources()
        assert [c["id"] for c in result] == ["cam-a"]

    def test_orphan_cameras_appear_as_removed(self, svc, store, storage_manager):
        # Paired list is empty, but a cam dir with a clip exists on disk.
        store.get_cameras.return_value = []
        _create_clip_file(storage_manager, "cam-orphan", "2026-04-09", "14-00-00")
        result, _, _ = svc.list_camera_sources()
        assert len(result) == 1
        assert result[0] == {
            "id": "cam-orphan",
            "name": "cam-orphan",
            "status": "removed",
        }

    def test_orphan_dir_without_mp4s_is_ignored(self, svc, store, storage_manager):
        from pathlib import Path

        store.get_cameras.return_value = []
        # Empty camera dir (no clips yet) — not an archive, skip.
        (Path(storage_manager.recordings_dir) / "cam-empty").mkdir()
        result, _, _ = svc.list_camera_sources()
        assert result == []

    def test_orphan_with_invalid_id_is_ignored(self, svc, store, storage_manager):
        from pathlib import Path

        store.get_cameras.return_value = []
        # Path-traversal-ish names must not be surfaced.
        bad = Path(storage_manager.recordings_dir) / "..weird"
        bad.mkdir()
        (bad / "2026-04-09").mkdir()
        (bad / "2026-04-09" / "14.mp4").write_bytes(b"x")
        result, _, _ = svc.list_camera_sources()
        assert result == []

    def test_paired_camera_does_not_double_as_orphan(self, svc, store, storage_manager):
        store.get_cameras.return_value = [
            Camera(id="cam-a", name="Front", status="online"),
        ]
        _create_clip_file(storage_manager, "cam-a", "2026-04-09", "14-00-00")
        result, _, _ = svc.list_camera_sources()
        assert [c["id"] for c in result] == ["cam-a"]
        assert result[0]["status"] == "online"


class TestOrphanBrowsing:
    """Orphan cameras (store returns None, files exist) must stay browseable."""

    def test_list_clips_works_for_orphan(self, svc, store, storage_manager):
        store.get_camera.return_value = None  # Camera record deleted
        _create_clip_file(storage_manager, "cam-orphan", "2026-04-09", "14-00-00")
        result, error, status = svc.list_clips("cam-orphan", "2026-04-09")
        assert error is None and status == 200
        assert len(result) == 1

    def test_list_dates_works_for_orphan(self, svc, store, storage_manager):
        store.get_camera.return_value = None
        _create_clip_file(storage_manager, "cam-orphan", "2026-04-09", "14-00-00")
        result, error, status = svc.list_dates("cam-orphan")
        assert status == 200
        assert result["dates"] == ["2026-04-09"]

    def test_unknown_cam_still_404s(self, svc, store):
        store.get_camera.return_value = None  # and no files on disk
        _, error, status = svc.list_clips("nope", "2026-04-09")
        assert status == 404 and error == "Camera not found"


class TestDeleteDate:
    """Bulk delete all clips for one camera on one date."""

    def test_invalid_camera_id(self, svc):
        _, error, status = svc.delete_date("../etc", "2026-04-09")
        assert status == 400 and error == "Invalid camera id"

    def test_invalid_date(self, svc):
        _, error, status = svc.delete_date("cam-001", "not-a-date")
        assert status == 400 and error == "Invalid date"

    def test_camera_not_found(self, svc, store):
        store.get_camera.return_value = None
        _, error, status = svc.delete_date("cam-001", "2026-04-09")
        assert status == 404

    def test_no_recordings_on_date(self, svc):
        _, error, status = svc.delete_date("cam-001", "2026-04-09")
        assert status == 404

    def test_deletes_all_clips_on_date(self, svc, storage_manager, audit):
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-00-00")
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "15-00-00")
        _create_clip_file(storage_manager, "cam-001", "2026-04-10", "09-00-00")
        result, error, status = svc.delete_date(
            "cam-001",
            "2026-04-09",
            requesting_user="admin",
            requesting_ip="127.0.0.1",
        )
        assert error is None and status == 200
        assert result["count"] == 2
        # The other date survives untouched.
        from pathlib import Path

        remaining = list(
            (Path(storage_manager.recordings_dir) / "cam-001" / "2026-04-10").glob(
                "*.mp4"
            )
        )
        assert len(remaining) == 1
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "CLIPS_DELETED"


class TestDeleteCameraRecordings:
    """Bulk delete all clips for a camera across every date."""

    def test_invalid_camera_id(self, svc):
        _, error, status = svc.delete_camera_recordings("../escape")
        assert status == 400

    def test_camera_not_found(self, svc, store):
        store.get_camera.return_value = None
        _, error, status = svc.delete_camera_recordings("nope")
        assert status == 404

    def test_deletes_entire_tree(self, svc, storage_manager, audit):
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-00-00")
        _create_clip_file(storage_manager, "cam-001", "2026-04-10", "09-00-00")
        result, error, status = svc.delete_camera_recordings(
            "cam-001",
            requesting_user="admin",
            requesting_ip="127.0.0.1",
        )
        assert error is None and status == 200
        assert result["count"] == 2
        from pathlib import Path

        cam_root = Path(storage_manager.recordings_dir) / "cam-001"
        assert not cam_root.exists()
        audit.log_event.assert_called_once()

    def test_orphan_cameras_are_deletable(self, svc, store, storage_manager):
        store.get_camera.return_value = None
        _create_clip_file(storage_manager, "cam-orphan", "2026-04-09", "14-00-00")
        result, error, status = svc.delete_camera_recordings("cam-orphan")
        assert error is None and status == 200
        assert result["count"] == 1


class TestDeleteAllRecordings:
    """Bulk delete every clip across every camera (issue #106)."""

    def test_returns_counts_and_wipes_every_camera(self, svc, storage_manager, audit):
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-00-00")
        _create_clip_file(storage_manager, "cam-001", "2026-04-10", "09-00-00")
        _create_clip_file(storage_manager, "cam-002", "2026-04-09", "12-30-00")

        result, error, status = svc.delete_all_recordings(
            requesting_user="admin",
            requesting_ip="127.0.0.1",
        )

        assert error is None and status == 200
        assert result["clips"] == 3
        assert result["cameras"] == 2
        assert result["bytes_freed"] >= 0

        from pathlib import Path

        root = Path(storage_manager.recordings_dir)
        assert not (root / "cam-001").exists()
        assert not (root / "cam-002").exists()

        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "RECORDINGS_DELETED_ALL"

    def test_no_recordings_dir_returns_zero_counts(self, svc, storage_manager, audit):
        import shutil
        from pathlib import Path

        shutil.rmtree(Path(storage_manager.recordings_dir), ignore_errors=True)

        result, error, status = svc.delete_all_recordings()

        assert error is None and status == 200
        assert result["clips"] == 0
        assert result["cameras"] == 0
        # No audit event when there's nothing to delete — stays clean.
        audit.log_event.assert_not_called()

    def test_skips_invalid_camera_id_directories(self, svc, storage_manager):
        """Stray directories that don't match the camera-id regex
        shouldn't be touched — protects against a sibling tool or test
        fixture that drops a scratch folder next to the camera trees."""
        from pathlib import Path

        root = Path(storage_manager.recordings_dir)
        # Name that fails _CAMERA_ID_RE (contains a ``.`` which the
        # regex doesn't allow) but is valid as a Windows/Linux path.
        stray = root / "stray.tmp"
        stray.mkdir(parents=True, exist_ok=True)
        (stray / "keep.txt").write_text("x")
        _create_clip_file(storage_manager, "cam-001", "2026-04-09", "14-00-00")

        result, _, _ = svc.delete_all_recordings()
        assert result["cameras"] == 1
        assert stray.exists()


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


class TestRecentAcrossCamerasLayouts:
    """Dashboard feed must handle both on-disk layouts.

    The loop recorder writes flat ``<cam>/YYYYMMDD_HHMMSS.mp4`` clips
    while older clips still sit in ``<cam>/YYYY-MM-DD/HH-MM-SS.mp4``.
    Regression: before the fix we parsed flat stems with the dated
    rule, producing ``date=<camera_id>`` and NaN timestamps on the UI.
    """

    def test_parses_flat_and_dated_layouts(self, svc, storage_manager):
        import os
        import time
        from pathlib import Path

        root = Path(storage_manager.recordings_dir)
        cam_dir = root / "cam-001"
        cam_dir.mkdir()
        # Flat clip (newest, but finalised — backdate past the active-write guard).
        flat = cam_dir / "20260417_101530.mp4"
        flat.write_bytes(b"x" * 10)
        flat_mtime = time.time() - 300
        os.utime(flat, (flat_mtime, flat_mtime))
        # Dated clip (older).
        dated_dir = cam_dir / "2026-04-10"
        dated_dir.mkdir()
        dated = dated_dir / "09-00-00.mp4"
        dated.write_bytes(b"x" * 10)

        old = time.time() - 3600
        os.utime(dated, (old, old))

        result, error, status = svc.recent_across_cameras(limit=10)
        assert error is None and status == 200
        assert len(result) == 2
        # Newest first.
        assert result[0]["filename"] == "20260417_101530.mp4"
        assert result[0]["date"] == "2026-04-17"
        assert result[0]["start_time"] == "10:15:30"
        # Dated layout still handled.
        assert result[1]["filename"] == "09-00-00.mp4"
        assert result[1]["date"] == "2026-04-10"
        assert result[1]["start_time"] == "09:00:00"

    def test_skips_unrecognised_stems(self, svc, storage_manager):
        import os
        import time
        from pathlib import Path

        root = Path(storage_manager.recordings_dir)
        cam_dir = root / "cam-001"
        cam_dir.mkdir()
        p = cam_dir / "weird-name.mp4"
        p.write_bytes(b"x")
        # Ensure mtime isn't within the active-write window so the skip
        # reason under test is the stem regex, not the freshness guard.
        old = time.time() - 300
        os.utime(p, (old, old))

        result, error, status = svc.recent_across_cameras(limit=10)
        assert error is None and status == 200
        assert result == []

    def test_in_progress_clip_hidden_from_feed(self, svc, storage_manager):
        """A clip ffmpeg is still writing has no moov atom yet — hiding it
        avoids the '0:00 blank player' bug when the operator clicks it on
        the dashboard."""
        import os
        import time
        from pathlib import Path

        root = Path(storage_manager.recordings_dir)
        cam_dir = root / "cam-001"
        cam_dir.mkdir()
        # Active clip: mtime = now (still being written).
        active = cam_dir / "20260418_120000.mp4"
        active.write_bytes(b"x")
        # Finalised clip: mtime well outside the active-write window.
        done = cam_dir / "20260418_115700.mp4"
        done.write_bytes(b"x")
        old = time.time() - 300
        os.utime(done, (old, old))

        result, error, status = svc.recent_across_cameras(limit=10)
        assert error is None and status == 200
        names = [r["filename"] for r in result]
        assert "20260418_120000.mp4" not in names
        assert "20260418_115700.mp4" in names


class TestResolveClipPathFlatFallback:
    def test_flat_clip_resolves_via_dated_url(self, svc, storage_manager):
        from pathlib import Path

        root = Path(storage_manager.recordings_dir)
        cam_dir = root / "cam-001"
        cam_dir.mkdir()
        flat = cam_dir / "20260417_101530.mp4"
        flat.write_bytes(b"x" * 10)

        # UI addresses the clip as /<cam>/<YYYY-MM-DD>/<filename>;
        # the service must find the flat file underneath.
        path, error, status = svc.resolve_clip_path(
            "cam-001", "2026-04-17", "20260417_101530.mp4"
        )
        assert error is None and status == 200
        assert path == flat.resolve()
