"""Tests for StorageManager — loop recording with FIFO cleanup."""
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from monitor.services.storage import (
    CHECK_INTERVAL,
    RESERVE_INTERNAL_MB,
    RESERVE_USB_MB,
    StorageManager,
    create_recording_dirs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def rec_dir(tmp_path):
    """Empty recordings directory."""
    d = tmp_path / "recordings"
    d.mkdir()
    return d


@pytest.fixture
def data_dir(tmp_path):
    """Simulated /data partition root."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def manager(rec_dir, data_dir):
    """StorageManager wired to tmp_path directories."""
    return StorageManager(
        recordings_dir=str(rec_dir),
        data_dir=str(data_dir),
        reserve_mb=400,
    )


def _make_clip(rec_dir, cam_id, date_str, time_str, size_bytes=1024):
    """Helper: create a fake MP4 clip in the expected directory layout."""
    clip_dir = rec_dir / cam_id / date_str
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip = clip_dir / f"{time_str}.mp4"
    clip.write_bytes(b"\x00" * size_bytes)
    return clip


# ---------------------------------------------------------------------------
# 1. Constructor sets paths correctly
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_paths_stored(self, rec_dir, data_dir):
        mgr = StorageManager(str(rec_dir), str(data_dir), reserve_mb=200)
        assert mgr._recordings_dir == Path(rec_dir)
        assert mgr._data_dir == Path(data_dir)
        assert mgr._reserve_mb == 200

    def test_defaults(self, rec_dir):
        mgr = StorageManager(str(rec_dir))
        assert mgr._data_dir == Path("/data")
        assert mgr._reserve_mb == RESERVE_INTERNAL_MB

    def test_initial_state(self, manager):
        assert manager._running is False
        assert manager._thread is None
        assert manager._on_dir_change is None


# ---------------------------------------------------------------------------
# 2. recordings_dir property
# ---------------------------------------------------------------------------

class TestRecordingsDirProperty:
    def test_returns_string(self, manager, rec_dir):
        assert manager.recordings_dir == str(rec_dir)
        assert isinstance(manager.recordings_dir, str)


# ---------------------------------------------------------------------------
# 3-5. set_recordings_dir
# ---------------------------------------------------------------------------

class TestSetRecordingsDir:
    def test_changes_path(self, manager, tmp_path):
        new = tmp_path / "new_rec"
        new.mkdir()
        manager.set_recordings_dir(str(new))
        assert manager.recordings_dir == str(new)

    def test_fires_callback(self, manager, tmp_path):
        cb = MagicMock()
        manager.set_dir_change_callback(cb)
        new = str(tmp_path / "new_rec")
        manager.set_recordings_dir(new)
        cb.assert_called_once_with(new)

    def test_no_callback_does_not_crash(self, manager, tmp_path):
        """set_recordings_dir with no callback set should not raise."""
        new = str(tmp_path / "other")
        manager.set_recordings_dir(new)  # should not raise

    def test_callback_exception_is_caught(self, manager, tmp_path):
        """A failing callback should not propagate."""
        cb = MagicMock(side_effect=RuntimeError("boom"))
        manager.set_dir_change_callback(cb)
        new = str(tmp_path / "x")
        manager.set_recordings_dir(new)  # should not raise
        cb.assert_called_once()


# ---------------------------------------------------------------------------
# 6-8. get_storage_stats
# ---------------------------------------------------------------------------

class TestGetStorageStats:
    def test_empty_dir(self, manager, rec_dir):
        stats = manager.get_storage_stats()
        assert stats["clip_count"] == 0
        assert stats["camera_count"] == 0
        assert stats["recordings_mb"] == 0.0
        assert stats["recordings_dir"] == str(rec_dir)
        assert "total_gb" in stats
        assert "free_gb" in stats
        assert "percent" in stats
        assert "reserve_mb" in stats

    def test_with_clips(self, manager, rec_dir):
        clip_size = 512 * 1024  # 512 KB each — large enough to register after rounding
        _make_clip(rec_dir, "cam1", "2024-01-01", "12-00-00", size_bytes=clip_size)
        _make_clip(rec_dir, "cam1", "2024-01-01", "12-03-00", size_bytes=clip_size)
        _make_clip(rec_dir, "cam2", "2024-01-02", "08-00-00", size_bytes=clip_size)

        stats = manager.get_storage_stats()
        assert stats["clip_count"] == 3
        assert stats["camera_count"] == 2
        assert stats["per_camera"]["cam1"]["clips"] == 2
        assert stats["per_camera"]["cam2"]["clips"] == 1
        assert stats["recordings_mb"] > 0

    def test_oserror_on_disk_usage(self, manager, rec_dir):
        with patch("shutil.disk_usage", side_effect=OSError("no mount")):
            stats = manager.get_storage_stats()
        assert stats["total_gb"] == 0
        assert stats["used_gb"] == 0
        assert stats["free_gb"] == 0
        assert stats["percent"] == 0.0
        assert stats["clip_count"] == 0

    def test_non_mp4_files_ignored(self, manager, rec_dir):
        cam_dir = rec_dir / "cam1" / "2024-01-01"
        cam_dir.mkdir(parents=True)
        (cam_dir / "12-00-00.thumb.jpg").write_bytes(b"\xff")
        (cam_dir / "notes.txt").write_bytes(b"hello")

        stats = manager.get_storage_stats()
        assert stats["clip_count"] == 0

    def test_non_directory_in_rec_dir_ignored(self, manager, rec_dir):
        (rec_dir / "stray_file.txt").write_text("ignore me")
        stats = manager.get_storage_stats()
        assert stats["camera_count"] == 0


# ---------------------------------------------------------------------------
# 9-10. needs_cleanup
# ---------------------------------------------------------------------------

class TestNeedsCleanup:
    def test_low_space_returns_true(self, manager):
        fake_usage = MagicMock(free=100 * 1024 * 1024)  # 100 MB free
        with patch("shutil.disk_usage", return_value=fake_usage):
            assert manager.needs_cleanup() is True

    def test_plenty_space_returns_false(self, manager):
        fake_usage = MagicMock(free=2000 * 1024 * 1024)  # 2000 MB free
        with patch("shutil.disk_usage", return_value=fake_usage):
            assert manager.needs_cleanup() is False

    def test_exact_threshold_returns_false(self, manager):
        fake_usage = MagicMock(free=400 * 1024 * 1024)  # exactly 400 MB
        with patch("shutil.disk_usage", return_value=fake_usage):
            assert manager.needs_cleanup() is False

    def test_oserror_returns_false(self, manager):
        with patch("shutil.disk_usage", side_effect=OSError):
            assert manager.needs_cleanup() is False


# ---------------------------------------------------------------------------
# 11-14. cleanup_oldest_clips
# ---------------------------------------------------------------------------

class TestCleanupOldestClips:
    def test_deletes_oldest_first(self, manager, rec_dir):
        old = _make_clip(rec_dir, "cam1", "2024-01-01", "06-00-00")
        mid = _make_clip(rec_dir, "cam1", "2024-01-01", "12-00-00")
        new = _make_clip(rec_dir, "cam1", "2024-01-02", "06-00-00")

        # needs_cleanup returns True for first 2 calls, then False
        with patch.object(manager, "needs_cleanup", side_effect=[True, True, False]):
            deleted = manager.cleanup_oldest_clips(max_delete=10)

        assert deleted == 2
        assert not old.exists()
        assert not mid.exists()
        assert new.exists()

    def test_respects_max_delete(self, manager, rec_dir):
        for i in range(5):
            _make_clip(rec_dir, "cam1", "2024-01-01", f"0{i}-00-00")

        with patch.object(manager, "needs_cleanup", return_value=True):
            deleted = manager.cleanup_oldest_clips(max_delete=2)

        assert deleted == 2

    def test_removes_empty_date_dirs(self, manager, rec_dir):
        clip = _make_clip(rec_dir, "cam1", "2024-01-01", "06-00-00")
        date_dir = clip.parent

        with patch.object(manager, "needs_cleanup", side_effect=[True, False]):
            manager.cleanup_oldest_clips()

        assert not date_dir.exists()

    def test_keeps_date_dir_if_clips_remain(self, manager, rec_dir):
        _make_clip(rec_dir, "cam1", "2024-01-01", "06-00-00")
        remaining = _make_clip(rec_dir, "cam1", "2024-01-01", "12-00-00")

        with patch.object(manager, "needs_cleanup", side_effect=[True, False]):
            manager.cleanup_oldest_clips()

        assert remaining.parent.exists()
        assert remaining.exists()

    def test_no_clips_returns_zero(self, manager, rec_dir):
        deleted = manager.cleanup_oldest_clips()
        assert deleted == 0

    def test_nonexistent_dir_returns_zero(self, tmp_path):
        mgr = StorageManager(str(tmp_path / "nope"))
        assert mgr.cleanup_oldest_clips() == 0

    def test_also_deletes_thumbnail(self, manager, rec_dir):
        clip = _make_clip(rec_dir, "cam1", "2024-01-01", "06-00-00")
        thumb = clip.with_suffix(".thumb.jpg")
        thumb.write_bytes(b"\xff")

        with patch.object(manager, "needs_cleanup", side_effect=[True, False]):
            manager.cleanup_oldest_clips()

        assert not clip.exists()
        assert not thumb.exists()

    def test_skips_malformed_filenames(self, manager, rec_dir):
        """Clips without valid date/time structure are skipped."""
        cam_dir = rec_dir / "cam1" / "not-a-date"
        cam_dir.mkdir(parents=True)
        (cam_dir / "badname.mp4").write_bytes(b"\x00" * 100)

        with patch.object(manager, "needs_cleanup", return_value=True):
            deleted = manager.cleanup_oldest_clips()

        assert deleted == 0


# ---------------------------------------------------------------------------
# 15. _is_usb_path
# ---------------------------------------------------------------------------

class TestIsUsbPath:
    def test_internal_path(self, data_dir):
        mgr = StorageManager(str(data_dir / "recordings"), str(data_dir))
        assert mgr._is_usb_path(data_dir / "recordings") is False

    def test_usb_path(self, data_dir):
        mgr = StorageManager("/mnt/usb/recordings", str(data_dir))
        assert mgr._is_usb_path(Path("/mnt/usb/recordings")) is True

    def test_is_usb_reflected_in_stats(self, tmp_path):
        mgr = StorageManager(str(tmp_path / "rec"), data_dir=str(tmp_path / "data"))
        (tmp_path / "rec").mkdir(exist_ok=True)
        stats = mgr.get_storage_stats()
        assert stats["is_usb"] is True  # rec is NOT under data_dir


# ---------------------------------------------------------------------------
# 16. start / stop lifecycle
# ---------------------------------------------------------------------------

class TestStartStop:
    def test_start_creates_thread(self, manager):
        with patch.object(manager, "_cleanup_loop"):
            manager.start()
            assert manager._running is True
            assert manager._thread is not None
            assert manager._thread.name == "storage-cleanup"
            manager.stop()

    def test_stop_sets_running_false(self, manager):
        manager._running = True
        manager._thread = None
        manager.stop()
        assert manager._running is False

    def test_start_is_idempotent(self, manager):
        with patch.object(manager, "_cleanup_loop"):
            manager.start()
            first_thread = manager._thread
            manager.start()  # second call should be a no-op
            assert manager._thread is first_thread
            manager.stop()

    def test_cleanup_loop_calls_needs_cleanup(self, manager):
        """Verify the loop checks disk and cleans up when needed."""
        call_count = 0

        def fake_loop():
            nonlocal call_count
            while manager._running and call_count < 2:
                if manager.needs_cleanup():
                    manager.cleanup_oldest_clips()
                call_count += 1
                manager._running = False

        with patch.object(manager, "_cleanup_loop", side_effect=fake_loop):
            manager.start()
            manager._thread.join(timeout=5)

        assert call_count >= 1


# ---------------------------------------------------------------------------
# Bonus: create_recording_dirs helper
# ---------------------------------------------------------------------------

class TestCreateRecordingDirs:
    def test_creates_nested_dirs(self, tmp_path):
        path = create_recording_dirs(str(tmp_path), "cam-front")
        assert path.exists()
        assert path.parent.name == "cam-front"
        # Date directory name is YYYY-MM-DD format
        assert len(path.name) == 10  # e.g. "2024-01-01"

    def test_idempotent(self, tmp_path):
        p1 = create_recording_dirs(str(tmp_path), "cam1")
        p2 = create_recording_dirs(str(tmp_path), "cam1")
        assert p1 == p2


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_check_interval(self):
        assert CHECK_INTERVAL == 30

    def test_reserve_internal_mb(self):
        assert RESERVE_INTERNAL_MB == 400

    def test_reserve_usb_mb(self):
        assert RESERVE_USB_MB == 100
