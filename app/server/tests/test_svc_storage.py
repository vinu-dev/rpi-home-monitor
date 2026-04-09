"""Tests for the storage management service."""
import os
from monitor.services.storage import StorageManager


def _make_clip(base, camera_id, clip_date, time_str, size=1024):
    """Helper: create a fake clip file."""
    clip_dir = base / camera_id / clip_date
    clip_dir.mkdir(parents=True, exist_ok=True)
    mp4 = clip_dir / f"{time_str}.mp4"
    mp4.write_bytes(b"x" * size)
    return mp4


class TestGetStorageStats:
    """Test storage statistics."""

    def test_empty_directory(self, tmp_path):
        mgr = StorageManager(str(tmp_path))
        stats = mgr.get_storage_stats()
        assert stats["total_gb"] > 0
        assert stats["clip_count"] == 0
        assert stats["camera_count"] == 0

    def test_counts_clips(self, tmp_path):
        rec = tmp_path / "recordings"
        rec.mkdir()
        _make_clip(rec, "cam-001", "2026-04-09", "14-00-00")
        _make_clip(rec, "cam-001", "2026-04-09", "14-30-00")
        _make_clip(rec, "cam-002", "2026-04-09", "14-00-00")
        mgr = StorageManager(str(rec))
        stats = mgr.get_storage_stats()
        assert stats["clip_count"] == 3
        assert stats["camera_count"] == 2
        assert stats["per_camera"]["cam-001"] == 2
        assert stats["per_camera"]["cam-002"] == 1

    def test_nonexistent_directory(self, tmp_path):
        mgr = StorageManager(str(tmp_path / "nonexistent"))
        stats = mgr.get_storage_stats()
        assert stats["clip_count"] == 0


class TestCleanupOldClips:
    """Test cleanup of old recordings."""

    def test_no_cleanup_under_threshold(self, tmp_path):
        rec = tmp_path / "recordings"
        rec.mkdir()
        _make_clip(rec, "cam-001", "2020-01-01", "10-00-00")
        # Set threshold to 100% so cleanup is never triggered
        mgr = StorageManager(str(rec), threshold_percent=100)
        deleted = mgr.cleanup_old_clips(min_age_hours=0)
        assert deleted == 0

    def test_nonexistent_directory(self, tmp_path):
        mgr = StorageManager(str(tmp_path / "nonexistent"))
        assert mgr.cleanup_old_clips() == 0
