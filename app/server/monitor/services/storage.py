"""
Storage management service — handles loop recording and cleanup.

Responsibilities:
- Monitor /data partition usage every 60 seconds
- When usage exceeds threshold (default 90%):
  - Find and delete oldest clips across all cameras
  - Never delete clips < 24 hours old (stop recording instead)
- Provide storage stats: total, used, free, per-camera breakdown
- Track clip count, oldest clip date, newest clip date
"""
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path


class StorageManager:
    """Manages disk space by cleaning up old recordings."""

    def __init__(self, recordings_dir: str, threshold_percent: int = 90):
        self._recordings_dir = Path(recordings_dir)
        self._threshold = threshold_percent

    def get_storage_stats(self) -> dict:
        """Get storage usage stats for the recordings partition."""
        try:
            usage = shutil.disk_usage(str(self._recordings_dir))
            total_gb = round(usage.total / (1024 ** 3), 1)
            used_gb = round(usage.used / (1024 ** 3), 1)
            free_gb = round(usage.free / (1024 ** 3), 1)
            percent = round(usage.used / usage.total * 100, 1) if usage.total > 0 else 0.0
        except OSError:
            return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0.0,
                    "camera_count": 0, "clip_count": 0}

        # Count clips per camera
        camera_stats = {}
        total_clips = 0
        if self._recordings_dir.is_dir():
            for cam_dir in self._recordings_dir.iterdir():
                if not cam_dir.is_dir():
                    continue
                count = sum(1 for _ in cam_dir.rglob("*.mp4"))
                camera_stats[cam_dir.name] = count
                total_clips += count

        return {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
            "camera_count": len(camera_stats),
            "clip_count": total_clips,
            "per_camera": camera_stats,
        }

    def cleanup_old_clips(self, min_age_hours: int = 24) -> int:
        """Delete oldest clips when over threshold.

        Never deletes clips newer than min_age_hours.
        Returns the number of clips deleted.
        """
        if not self._recordings_dir.is_dir():
            return 0

        try:
            usage = shutil.disk_usage(str(self._recordings_dir))
            percent = usage.used / usage.total * 100 if usage.total > 0 else 0
        except OSError:
            return 0

        if percent <= self._threshold:
            return 0

        # Collect all clips with their dates
        cutoff = datetime.now() - timedelta(hours=min_age_hours)
        clips = []
        for mp4 in self._recordings_dir.rglob("*.mp4"):
            # Path: recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.mp4
            parts = mp4.parts
            if len(parts) < 3:
                continue
            date_str = parts[-2]
            time_str = mp4.stem
            try:
                clip_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H-%M-%S")
            except ValueError:
                continue
            if clip_dt < cutoff:
                clips.append((clip_dt, mp4))

        # Sort oldest first and delete until under threshold
        clips.sort(key=lambda x: x[0])
        deleted = 0
        for _, mp4 in clips:
            mp4.unlink(missing_ok=True)
            thumb = mp4.with_suffix(".thumb.jpg")
            if thumb.exists():
                thumb.unlink()
            deleted += 1

            # Check if we're under threshold now
            try:
                usage = shutil.disk_usage(str(self._recordings_dir))
                percent = usage.used / usage.total * 100 if usage.total > 0 else 0
            except OSError:
                break
            if percent <= self._threshold:
                break

        return deleted
