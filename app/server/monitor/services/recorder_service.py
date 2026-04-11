"""
Recording service — manages ffmpeg processes for video clip recording.

Responsibilities:
- One ffmpeg process per active camera
- RTSPS input -> dual output:
  - HLS segments for live view (.m3u8 + .ts, 2s segments, rolling 5)
  - MP4 clips for recording (3-minute segments, faststart)
- Generate thumbnail JPEG for each completed clip
- Handle camera disconnect/reconnect gracefully
- Respect recording mode per camera (continuous/off)

File layout:
  /data/recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.mp4
  /data/recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.thumb.jpg
  /data/live/<cam-id>/stream.m3u8
  /data/live/<cam-id>/segment_NNN.ts
"""

from datetime import date
from pathlib import Path

from monitor.models import Clip


class RecorderService:
    """Manages recording state and clip metadata.

    The actual ffmpeg processes are started on RPi hardware only.
    This class provides the clip management layer used by the
    recordings API.
    """

    def __init__(self, recordings_dir: str, live_dir: str):
        self._recordings_dir = Path(recordings_dir)
        self._live_dir = Path(live_dir)

    def list_clips(self, camera_id: str, clip_date: str = "") -> list[Clip]:
        """List recorded clips for a camera on a given date.

        If no date is given, uses today's date.
        Returns clips sorted by start_time (ascending).
        """
        if not clip_date:
            clip_date = date.today().isoformat()

        cam_dir = self._recordings_dir / camera_id / clip_date
        if not cam_dir.is_dir():
            return []

        clips = []
        for mp4 in sorted(cam_dir.glob("*.mp4")):
            # Filename format: HH-MM-SS.mp4
            stem = mp4.stem  # e.g. "14-30-00"
            start_time = stem.replace("-", ":")
            thumb = mp4.with_suffix(".thumb.jpg")

            clips.append(
                Clip(
                    camera_id=camera_id,
                    filename=mp4.name,
                    date=clip_date,
                    start_time=start_time,
                    size_bytes=mp4.stat().st_size,
                    thumbnail=thumb.name if thumb.exists() else "",
                )
            )

        return clips

    def get_clip_path(self, camera_id: str, clip_date: str, filename: str):
        """Get the full path to a clip file. Returns None if not found."""
        path = self._recordings_dir / camera_id / clip_date / filename
        if path.is_file():
            return path
        return None

    def delete_clip(self, camera_id: str, clip_date: str, filename: str) -> bool:
        """Delete a clip and its thumbnail. Returns True if deleted."""
        path = self._recordings_dir / camera_id / clip_date / filename
        if not path.is_file():
            return False

        path.unlink()

        # Also remove thumbnail
        thumb = path.with_suffix(".thumb.jpg")
        if thumb.exists():
            thumb.unlink()

        # Remove empty date directory
        date_dir = path.parent
        if date_dir.is_dir() and not any(date_dir.iterdir()):
            date_dir.rmdir()

        return True

    def get_dates_with_clips(self, camera_id: str) -> list[str]:
        """List dates that have recordings for a camera."""
        cam_dir = self._recordings_dir / camera_id
        if not cam_dir.is_dir():
            return []

        dates = []
        for d in sorted(cam_dir.iterdir()):
            if d.is_dir() and any(d.glob("*.mp4")):
                dates.append(d.name)
        return dates

    def get_latest_clip(self, camera_id: str):
        """Get the most recent clip for a camera. Returns None if no clips."""
        dates = self.get_dates_with_clips(camera_id)
        if not dates:
            return None

        clips = self.list_clips(camera_id, dates[-1])
        if not clips:
            return None

        return clips[-1]
