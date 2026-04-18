"""Unit tests for StorageManager — FIFO cleanup, stats, dir-change callback."""

import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from monitor.services.storage_manager import StorageManager, create_recording_dirs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clip(rec_dir: Path, cam_id: str, date: str, time_str: str, size_bytes: int = 1024) -> Path:
    """Create a fake .mp4 clip file at the expected path."""
    clip_dir = rec_dir / cam_id / date
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip = clip_dir / f"{time_str}.mp4"
    clip.write_bytes(b"x" * size_bytes)
    return clip


def _make_manager(tmp_path, reserve_mb=0, threshold_percent=None):
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    return StorageManager(
        recordings_dir=str(rec_dir),
        data_dir=str(tmp_path),
        reserve_mb=reserve_mb,
        threshold_percent=threshold_percent,
    ), rec_dir


# ===========================================================================
# Construction and properties
# ===========================================================================


class TestConstruction:
    def test_recordings_dir_property(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path)
        assert mgr.recordings_dir == str(rec_dir)

    def test_not_running_on_creation(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        assert mgr._running is False

    def test_set_recordings_dir(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        new_dir = str(tmp_path / "usb_recordings")
        mgr.set_recordings_dir(new_dir)
        assert mgr.recordings_dir == new_dir

    def test_set_recordings_dir_triggers_callback(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        cb = MagicMock()
        mgr.set_dir_change_callback(cb)
        new_dir = str(tmp_path / "usb")
        mgr.set_recordings_dir(new_dir)
        cb.assert_called_once_with(new_dir)

    def test_callback_exception_does_not_propagate(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        mgr.set_dir_change_callback(MagicMock(side_effect=RuntimeError("boom")))
        # Should not raise
        mgr.set_recordings_dir(str(tmp_path / "usb"))
        assert mgr.recordings_dir == str(tmp_path / "usb")

    def test_set_threshold_percent(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        mgr.set_threshold_percent(75)
        assert mgr._threshold_percent == 75

    def test_set_threshold_percent_to_none(self, tmp_path):
        mgr, _ = _make_manager(tmp_path, threshold_percent=80)
        mgr.set_threshold_percent(None)
        assert mgr._threshold_percent is None


# ===========================================================================
# needs_cleanup
# ===========================================================================


class TestNeedsCleanup:
    def test_false_when_enough_free_space(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, reserve_mb=0)
        assert mgr.needs_cleanup() is False

    def test_true_when_threshold_exceeded(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=0)
        # 0% threshold means always needs cleanup as long as any bytes are used
        # Patch shutil.disk_usage to return a 1GB disk that's 50% used
        with patch("monitor.services.storage_manager.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(
                total=1_000_000_000, used=500_000_000, free=500_000_000
            )
            mgr.set_threshold_percent(40)
            assert mgr.needs_cleanup() is True

    def test_false_when_below_threshold(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=90)
        with patch("monitor.services.storage_manager.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(
                total=1_000_000_000, used=500_000_000, free=500_000_000
            )
            assert mgr.needs_cleanup() is False

    def test_false_on_oserror(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        with patch("monitor.services.storage_manager.shutil.disk_usage", side_effect=OSError):
            assert mgr.needs_cleanup() is False

    def test_uses_reserve_mb_when_no_threshold(self, tmp_path):
        mgr, _ = _make_manager(tmp_path, reserve_mb=500)
        with patch("monitor.services.storage_manager.shutil.disk_usage") as mock_du:
            # 100MB free < 500MB reserve → needs cleanup
            mock_du.return_value = MagicMock(
                total=1_000_000_000,
                used=900_000_000,
                free=100 * 1024 * 1024,
            )
            assert mgr.needs_cleanup() is True

    def test_does_not_need_cleanup_with_ample_free_space(self, tmp_path):
        mgr, _ = _make_manager(tmp_path, reserve_mb=100)
        with patch("monitor.services.storage_manager.shutil.disk_usage") as mock_du:
            # 1GB free > 100MB reserve → no cleanup
            mock_du.return_value = MagicMock(
                total=2_000_000_000,
                used=1_000_000_000,
                free=1_000 * 1024 * 1024,
            )
            assert mgr.needs_cleanup() is False


# ===========================================================================
# cleanup_oldest_clips — FIFO ordering
# ===========================================================================


class TestCleanupOldestClips:
    def test_returns_zero_when_no_dir(self, tmp_path):
        mgr = StorageManager(
            recordings_dir=str(tmp_path / "nonexistent"),
            data_dir=str(tmp_path),
        )
        assert mgr.cleanup_oldest_clips() == 0

    def test_returns_zero_when_empty(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=0)
        with patch.object(mgr, "needs_cleanup", return_value=True):
            assert mgr.cleanup_oldest_clips() == 0

    def test_deletes_oldest_first(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=0)
        old = _make_clip(rec_dir, "cam-001", "2026-01-01", "00-00-00")
        new = _make_clip(rec_dir, "cam-001", "2026-04-01", "12-00-00")

        with patch.object(mgr, "needs_cleanup", side_effect=[True, False]):
            deleted = mgr.cleanup_oldest_clips()

        assert deleted == 1
        assert not old.exists(), "Oldest clip should have been deleted"
        assert new.exists(), "Newer clip should still exist"

    def test_stops_when_cleanup_not_needed(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=90)
        for i in range(5):
            _make_clip(rec_dir, "cam-001", "2026-01-01", f"0{i}-00-00")
        # needs_cleanup returns False immediately → nothing deleted
        with patch.object(mgr, "needs_cleanup", return_value=False):
            deleted = mgr.cleanup_oldest_clips()
        assert deleted == 0

    def test_respects_max_delete_limit(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=0)
        for i in range(10):
            _make_clip(rec_dir, "cam-001", "2026-01-01", f"{i:02d}-00-00")
        with patch.object(mgr, "needs_cleanup", return_value=True):
            deleted = mgr.cleanup_oldest_clips(max_delete=3)
        assert deleted == 3

    def test_also_deletes_thumbnail(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=0)
        clip = _make_clip(rec_dir, "cam-001", "2026-01-01", "08-00-00")
        thumb = clip.with_suffix(".thumb.jpg")
        thumb.write_bytes(b"thumb")

        with patch.object(mgr, "needs_cleanup", side_effect=[True, False]):
            mgr.cleanup_oldest_clips()

        assert not clip.exists()
        assert not thumb.exists()

    def test_removes_empty_date_directory(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=0)
        clip = _make_clip(rec_dir, "cam-001", "2026-01-01", "08-00-00")
        date_dir = clip.parent

        with patch.object(mgr, "needs_cleanup", side_effect=[True, False]):
            mgr.cleanup_oldest_clips()

        assert not date_dir.exists(), "Empty date dir should be removed"

    def test_does_not_remove_non_empty_date_directory(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=0)
        clip1 = _make_clip(rec_dir, "cam-001", "2026-01-01", "08-00-00")
        _make_clip(rec_dir, "cam-001", "2026-01-01", "09-00-00")

        with patch.object(mgr, "needs_cleanup", side_effect=[True, False]):
            mgr.cleanup_oldest_clips()

        assert clip1.parent.exists(), "Non-empty date dir should remain"

    def test_skips_files_with_unparseable_names(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=0)
        junk_dir = rec_dir / "cam-001" / "2026-01-01"
        junk_dir.mkdir(parents=True)
        (junk_dir / "not-a-timestamp.mp4").write_bytes(b"x")
        # No parseable clips → nothing deleted, no crash
        with patch.object(mgr, "needs_cleanup", return_value=True):
            deleted = mgr.cleanup_oldest_clips()
        assert deleted == 0

    def test_deletes_across_multiple_cameras_oldest_first(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path, threshold_percent=0)
        cam1_old = _make_clip(rec_dir, "cam-001", "2026-01-01", "06-00-00")
        _make_clip(rec_dir, "cam-002", "2026-03-01", "06-00-00")

        with patch.object(mgr, "needs_cleanup", side_effect=[True, False]):
            deleted = mgr.cleanup_oldest_clips()

        assert deleted == 1
        assert not cam1_old.exists()


# ===========================================================================
# get_storage_stats
# ===========================================================================


class TestGetStorageStats:
    def test_returns_dict_with_required_keys(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        stats = mgr.get_storage_stats()
        for key in ("total_gb", "used_gb", "free_gb", "percent", "camera_count",
                    "clip_count", "recordings_dir", "is_usb"):
            assert key in stats, f"Missing key: {key}"

    def test_counts_clips_per_camera(self, tmp_path):
        mgr, rec_dir = _make_manager(tmp_path)
        _make_clip(rec_dir, "cam-001", "2026-04-01", "08-00-00")
        _make_clip(rec_dir, "cam-001", "2026-04-01", "09-00-00")
        _make_clip(rec_dir, "cam-002", "2026-04-01", "10-00-00")
        stats = mgr.get_storage_stats()
        assert stats["clip_count"] == 3
        assert stats["camera_count"] == 2
        assert stats["per_camera"]["cam-001"]["clips"] == 2
        assert stats["per_camera"]["cam-002"]["clips"] == 1

    def test_returns_zeros_on_oserror(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        with patch("monitor.services.storage_manager.shutil.disk_usage", side_effect=OSError):
            stats = mgr.get_storage_stats()
        assert stats["total_gb"] == 0
        assert stats["used_gb"] == 0

    def test_is_usb_false_for_internal_path(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        stats = mgr.get_storage_stats()
        assert stats["is_usb"] is False

    def test_is_usb_true_for_external_path(self, tmp_path):
        rec_dir = tmp_path / "recordings"
        rec_dir.mkdir()
        mgr = StorageManager(
            recordings_dir="/mnt/usb/recordings",
            data_dir=str(tmp_path),
        )
        stats = mgr.get_storage_stats()
        assert stats["is_usb"] is True


# ===========================================================================
# start / stop lifecycle
# ===========================================================================


class TestLifecycle:
    def test_start_creates_thread(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        mgr.start()
        try:
            assert mgr._running is True
            assert mgr._thread is not None
            assert mgr._thread.is_alive()
        finally:
            mgr.stop()

    def test_double_start_is_safe(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        mgr.start()
        thread1 = mgr._thread
        mgr.start()
        assert mgr._thread is thread1, "Second start should not create a new thread"
        mgr.stop()

    def test_stop_joins_thread(self, tmp_path):
        mgr, _ = _make_manager(tmp_path)
        mgr.start()
        mgr.stop()
        assert mgr._running is False
        assert not mgr._thread.is_alive()


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_set_recordings_dir(self, tmp_path):
        """Concurrent directory changes must not corrupt the internal path."""
        mgr, _ = _make_manager(tmp_path)
        errors = []

        def change_dir(i):
            try:
                mgr.set_recordings_dir(str(tmp_path / f"dir_{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=change_dir, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Final state must be a valid string
        assert isinstance(mgr.recordings_dir, str)


# ===========================================================================
# create_recording_dirs helper
# ===========================================================================


class TestCreateRecordingDirs:
    def test_creates_today_subdir(self, tmp_path):
        from datetime import datetime

        rec_dir = tmp_path / "recordings"
        rec_dir.mkdir()
        path = create_recording_dirs(str(rec_dir), "cam-001")
        today = datetime.now().strftime("%Y-%m-%d")
        assert path.is_dir()
        assert path.name == today
        assert path.parent.name == "cam-001"

    def test_idempotent_when_dir_exists(self, tmp_path):
        rec_dir = tmp_path / "recordings"
        rec_dir.mkdir()
        path1 = create_recording_dirs(str(rec_dir), "cam-001")
        path2 = create_recording_dirs(str(rec_dir), "cam-001")
        assert path1 == path2
        assert path1.is_dir()
