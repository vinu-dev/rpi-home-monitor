"""
Storage management service — loop recording with FIFO cleanup.

Runs a background thread that monitors disk usage every 60 seconds.
When usage exceeds the threshold, deletes oldest clips first (FIFO).

Supports two recording locations:
- Internal /data partition (default, 400MB reserved for OS/config)
- External USB storage (full capacity minus small buffer)

When USB is configured but removed, falls back to /data automatically.

Design patterns:
- Constructor Injection (recordings_dir, data_dir, reserve_mb)
- Fail-Silent (all disk ops wrapped in try/except)
- Single Responsibility (only manages storage, not streaming)
"""

import logging
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("monitor.storage")

CHECK_INTERVAL = 30  # seconds between cleanup checks
RESERVE_INTERNAL_MB = 400  # keep this much free on /data for OS/config/logs
RESERVE_USB_MB = 100  # keep this much free on USB for filesystem overhead


class StorageManager:
    """Manages recording storage with FIFO loop cleanup.

    Args:
        recordings_dir: Path to recordings directory.
        data_dir: Path to /data partition (for internal space calculation).
        reserve_mb: MB to keep free on internal storage.
    """

    def __init__(
        self,
        recordings_dir: str,
        data_dir: str = "/data",
        reserve_mb: int = RESERVE_INTERNAL_MB,
    ):
        self._recordings_dir = Path(recordings_dir)
        self._data_dir = Path(data_dir)
        self._reserve_mb = reserve_mb
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        # Callback to notify streaming service when recordings dir changes
        self._on_dir_change = None

    @property
    def recordings_dir(self) -> str:
        """Current recordings directory path."""
        with self._lock:
            return str(self._recordings_dir)

    def set_recordings_dir(self, new_dir: str):
        """Change the recordings directory (e.g., switching to USB).

        Calls the on_dir_change callback if set, so streaming service
        can restart recorders with the new path.
        """
        with self._lock:
            old_dir = str(self._recordings_dir)
            self._recordings_dir = Path(new_dir)
        log.info("Recordings directory changed: %s -> %s", old_dir, new_dir)
        if self._on_dir_change:
            try:
                self._on_dir_change(new_dir)
            except Exception as e:
                log.error("Error in dir change callback: %s", e)

    def set_dir_change_callback(self, callback):
        """Set callback(new_dir: str) for when recordings dir changes."""
        self._on_dir_change = callback

    def start(self):
        """Start the background cleanup thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._cleanup_loop, daemon=True, name="storage-cleanup"
        )
        self._thread.start()
        log.info(
            "Storage manager started (reserve=%dMB, dir=%s)",
            self._reserve_mb,
            self._recordings_dir,
        )

    def stop(self):
        """Stop the background cleanup thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        log.info("Storage manager stopped")

    def get_storage_stats(self) -> dict:
        """Get storage usage stats for the current recordings location."""
        rec_dir = self._recordings_dir
        try:
            usage = shutil.disk_usage(str(rec_dir))
            total_gb = round(usage.total / (1024**3), 2)
            used_gb = round(usage.used / (1024**3), 2)
            free_gb = round(usage.free / (1024**3), 2)
            percent = (
                round(usage.used / usage.total * 100, 1) if usage.total > 0 else 0.0
            )
        except OSError:
            return {
                "total_gb": 0,
                "used_gb": 0,
                "free_gb": 0,
                "percent": 0.0,
                "camera_count": 0,
                "clip_count": 0,
                "recordings_dir": str(rec_dir),
                "is_usb": self._is_usb_path(rec_dir),
            }

        # Count clips per camera
        camera_stats = {}
        total_clips = 0
        recordings_bytes = 0
        if rec_dir.is_dir():
            for cam_dir in rec_dir.iterdir():
                if not cam_dir.is_dir():
                    continue
                count = 0
                cam_bytes = 0
                for mp4 in cam_dir.rglob("*.mp4"):
                    count += 1
                    try:
                        cam_bytes += mp4.stat().st_size
                    except OSError:
                        pass
                camera_stats[cam_dir.name] = {
                    "clips": count,
                    "size_mb": round(cam_bytes / (1024 * 1024), 1),
                }
                total_clips += count
                recordings_bytes += cam_bytes

        return {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
            "recordings_mb": round(recordings_bytes / (1024 * 1024), 1),
            "camera_count": len(camera_stats),
            "clip_count": total_clips,
            "per_camera": camera_stats,
            "recordings_dir": str(rec_dir),
            "is_usb": self._is_usb_path(rec_dir),
            "reserve_mb": self._reserve_mb,
        }

    def needs_cleanup(self) -> bool:
        """Check if the recordings partition needs cleanup."""
        try:
            usage = shutil.disk_usage(str(self._recordings_dir))
            free_mb = usage.free / (1024 * 1024)
            return free_mb < self._reserve_mb
        except OSError:
            return False

    def cleanup_oldest_clips(self, max_delete: int = 50) -> int:
        """Delete oldest clips to free space. No minimum age — true FIFO.

        Args:
            max_delete: Max clips to delete per call (prevent long stalls).

        Returns number of clips deleted.
        """
        rec_dir = self._recordings_dir
        if not rec_dir.is_dir():
            return 0

        # Collect all clips with parsed timestamps
        clips = []
        for mp4 in rec_dir.rglob("*.mp4"):
            parts = mp4.parts
            if len(parts) < 3:
                continue
            date_str = parts[-2]  # YYYY-MM-DD
            time_str = mp4.stem  # HH-MM-SS
            try:
                clip_dt = datetime.strptime(
                    f"{date_str} {time_str}", "%Y-%m-%d %H-%M-%S"
                )
            except ValueError:
                continue
            clips.append((clip_dt, mp4))

        if not clips:
            return 0

        # Sort oldest first
        clips.sort(key=lambda x: x[0])

        deleted = 0
        for _, mp4 in clips:
            if deleted >= max_delete:
                break
            if not self.needs_cleanup():
                break

            try:
                size = mp4.stat().st_size
                mp4.unlink(missing_ok=True)
                # Also remove thumbnail if exists
                thumb = mp4.with_suffix(".thumb.jpg")
                thumb.unlink(missing_ok=True)
                deleted += 1
                log.info(
                    "Loop cleanup: deleted %s (%.1f MB)", mp4.name, size / (1024 * 1024)
                )
            except OSError as e:
                log.warning("Failed to delete %s: %s", mp4, e)

            # Clean up empty date directories
            try:
                date_dir = mp4.parent
                if date_dir.is_dir() and not any(date_dir.iterdir()):
                    date_dir.rmdir()
            except OSError:
                pass

        if deleted > 0:
            log.info("Loop cleanup: deleted %d clips", deleted)
        return deleted

    def _cleanup_loop(self):
        """Background thread: check disk usage every CHECK_INTERVAL."""
        while self._running:
            try:
                if self.needs_cleanup():
                    self.cleanup_oldest_clips()
            except Exception:
                log.exception("Error in storage cleanup loop")

            # Sleep in small increments for responsive shutdown
            for _ in range(CHECK_INTERVAL * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _is_usb_path(self, path) -> bool:
        """Check if path is on USB (not under /data)."""
        return not str(path).startswith(str(self._data_dir))


def create_recording_dirs(recordings_dir, cam_id):
    """Ensure recording directory exists with today's date subdirectory.

    Called by the recorder's segment pattern, but ffmpeg can't create
    nested dirs. This pre-creates today's directory.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    path = Path(recordings_dir) / cam_id / today
    path.mkdir(parents=True, exist_ok=True)
    return path
