# REQ: SWR-005, SWR-006; RISK: RISK-001; SEC: SC-002; TEST: TC-002
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

import re
from datetime import date
from pathlib import Path

from monitor.models import Clip
from monitor.services.clip_stamper import stamp_sentinel_path

# Loop recorder produces flat filenames directly under the camera dir:
#   cam-xxx/YYYYMMDD_HHMMSS.mp4
# Legacy dated layout uses a YYYY-MM-DD subdir with HH-MM-SS.mp4 inside.
# Writers today only produce the flat layout; readers must handle both
# because real devices still have legacy dated clips on disk. The
# recordings-service layer (recordings_service._parse_clip_date_time)
# already knows both, but the recorder-service read methods used by
# the Recordings page were dated-only — that's why the page showed
# "No recordings found" on devices whose clips are all flat-layout.
_FLAT_STEM_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$")
_DATED_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATED_STEM_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})$")


def _flat_clip_date(stem: str) -> str:
    """Return "YYYY-MM-DD" if ``stem`` is a flat-layout filename, else ""."""
    m = _FLAT_STEM_RE.match(stem)
    if not m:
        return ""
    y, mo, d, _, _, _ = m.groups()
    return f"{y}-{mo}-{d}"


def _flat_clip_start_time(stem: str) -> str:
    """Return "HH:MM:SS" if ``stem`` is a flat-layout filename, else ""."""
    m = _FLAT_STEM_RE.match(stem)
    if not m:
        return ""
    _, _, _, hh, mm, ss = m.groups()
    return f"{hh}:{mm}:{ss}"


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
        Handles both layouts (see module docstring): dated-subdir
        legacy clips and flat-layout loop-recorder clips sharing the
        camera directory.
        """
        if not clip_date:
            clip_date = date.today().isoformat()

        cam_dir = self._recordings_dir / camera_id
        if not cam_dir.is_dir():
            return []

        clips: list[Clip] = []

        # Dated layout: <cam>/YYYY-MM-DD/HH-MM-SS.mp4
        dated_dir = cam_dir / clip_date
        if dated_dir.is_dir():
            for mp4 in sorted(dated_dir.glob("*.mp4")):
                stem = mp4.stem  # "14-30-00"
                if not _DATED_STEM_RE.match(stem):
                    continue
                thumb = mp4.with_suffix(".thumb.jpg")
                clips.append(
                    Clip(
                        camera_id=camera_id,
                        filename=mp4.name,
                        date=clip_date,
                        start_time=stem.replace("-", ":"),
                        size_bytes=mp4.stat().st_size,
                        thumbnail=thumb.name if thumb.exists() else "",
                        stamped=stamp_sentinel_path(mp4).exists(),
                    )
                )

        # Flat layout: <cam>/YYYYMMDD_HHMMSS.mp4. Filter by requested date.
        for mp4 in sorted(cam_dir.glob("*.mp4")):
            if _flat_clip_date(mp4.stem) != clip_date:
                continue
            thumb = mp4.with_suffix(".thumb.jpg")
            clips.append(
                Clip(
                    camera_id=camera_id,
                    filename=mp4.name,
                    date=clip_date,
                    start_time=_flat_clip_start_time(mp4.stem),
                    size_bytes=mp4.stat().st_size,
                    thumbnail=thumb.name if thumb.exists() else "",
                    stamped=stamp_sentinel_path(mp4).exists(),
                )
            )

        clips.sort(key=lambda c: c.start_time)
        return clips

    def get_clip_path(self, camera_id: str, clip_date: str, filename: str):
        """Get the full path to a clip file. Returns None if not found.

        The UI always builds the dated URL (``/<cam>/<date>/<file>``) but
        files may live under either layout — probe both.
        """
        dated_path = self._recordings_dir / camera_id / clip_date / filename
        if dated_path.is_file():
            return dated_path
        flat_path = self._recordings_dir / camera_id / filename
        if flat_path.is_file():
            return flat_path
        return None

    def delete_clip(self, camera_id: str, clip_date: str, filename: str) -> bool:
        """Delete a clip and its thumbnail. Returns True if deleted.

        Handles both on-disk layouts:
          dated:  <recordings_dir>/<cam>/YYYY-MM-DD/HH-MM-SS.mp4
          flat:   <recordings_dir>/<cam>/YYYYMMDD_HHMMSS.mp4  (loop recorder)

        The UI always builds the dated URL shape, so we probe the dated
        path first and fall back to the flat path underneath the camera
        directory.
        """
        dated_path = self._recordings_dir / camera_id / clip_date / filename
        flat_path = self._recordings_dir / camera_id / filename
        if dated_path.is_file():
            path = dated_path
        elif flat_path.is_file():
            path = flat_path
        else:
            return False

        path.unlink()

        # Also remove thumbnail
        thumb = path.with_suffix(".thumb.jpg")
        if thumb.exists():
            thumb.unlink()

        # Remove empty date directory (dated layout only — the camera
        # directory itself is shared by flat clips and should survive).
        cam_dir = self._recordings_dir / camera_id
        parent = path.parent
        if parent != cam_dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

        return True

    def get_dates_with_clips(self, camera_id: str) -> list[str]:
        """List dates that have recordings for a camera.

        Merges both on-disk layouts:
          dated-subdir:  <cam>/YYYY-MM-DD/*.mp4   → YYYY-MM-DD
          flat:          <cam>/YYYYMMDD_*.mp4     → derive YYYY-MM-DD
        Result is deduped and sorted ascending.
        """
        cam_dir = self._recordings_dir / camera_id
        if not cam_dir.is_dir():
            return []

        dates: set[str] = set()
        try:
            for entry in cam_dir.iterdir():
                if entry.is_dir():
                    if _DATED_DIR_RE.match(entry.name) and any(entry.glob("*.mp4")):
                        dates.add(entry.name)
                elif entry.is_file() and entry.suffix == ".mp4":
                    d = _flat_clip_date(entry.stem)
                    if d:
                        dates.add(d)
        except OSError:
            return []

        return sorted(dates)

    def get_latest_clip(self, camera_id: str):
        """Get the most recent clip for a camera. Returns None if no clips."""
        dates = self.get_dates_with_clips(camera_id)
        if not dates:
            return None

        clips = self.list_clips(camera_id, dates[-1])
        if not clips:
            return None

        return clips[-1]
