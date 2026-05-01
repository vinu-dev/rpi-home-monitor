"""
SnapshotExtractor — best-effort frame extraction for motion notifications.

Implements ADR-0027 §"Snapshot pipeline." On motion phase=end with a
correlated clip, extract a single frame at started_at + 1.0s into a
sibling .jpg next to the clip's .mp4 so the browser notification can
show a still image of the action.

Failure modes:
  - clip not yet on disk (motion-mode pre-roll race) → skip; the
    notification fires text-only per spec
  - ffmpeg not in PATH → log once, never again; fall back to no-op
  - extraction crash → log, skip; the alert center inbox row is
    unaffected

Bounded cost: one synchronous ffmpeg invocation per qualifying motion
event. AlertCenterService's MOTION_NOTIFICATION_THRESHOLD + the
policy service's min_duration filter run BEFORE this is invoked, so
the noise floor never reaches the extractor.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("monitor.snapshot_extractor")

# Frame offset (seconds from clip start) for the still extract.
# 1.0s is past the encoder warm-up but well before the typical
# motion event ends.
DEFAULT_FRAME_OFFSET_SECONDS = 1.0
# JPEG quality for ffmpeg's -q:v: 1 = best, 31 = worst. 4 is a
# decent balance for ~50 KB stills suitable for OS notification
# icons.
DEFAULT_JPEG_QUALITY = 4
# ffmpeg call timeout — a healthy run completes in well under a
# second on a Pi 4B; cap at 10s so a hung run can't pile up.
EXTRACT_TIMEOUT_SECONDS = 10


class SnapshotExtractor:
    """Best-effort frame extraction at clip-time."""

    def __init__(
        self,
        recordings_dir: str | Path,
        *,
        ffmpeg_path: str | None = None,
        frame_offset_seconds: float = DEFAULT_FRAME_OFFSET_SECONDS,
    ):
        self._recordings_dir = Path(recordings_dir)
        self._ffmpeg = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self._frame_offset = float(frame_offset_seconds)
        # One-shot warning when ffmpeg can't be located so the log
        # doesn't fill up on a misconfigured box.
        self._warned_missing_ffmpeg = False

    def extract_for_clip(self, clip_ref: dict) -> str | None:
        """Try to produce a sibling .jpg next to the clip's .mp4.

        Args:
            clip_ref: shape ``{camera_id, date, filename}`` —
                same shape MotionEventStore stores.

        Returns:
            The relative path under recordings_dir (or None on
            failure / missing clip / missing ffmpeg).

        Idempotent: if the .jpg already exists and is non-empty,
        returns its path without re-running ffmpeg. Side effects
        only on the disk; never raises.
        """
        if not isinstance(clip_ref, dict):
            return None
        cam = clip_ref.get("camera_id") or ""
        date = clip_ref.get("date") or ""
        filename = clip_ref.get("filename") or ""
        if not (cam and date and filename and filename.endswith(".mp4")):
            return None

        clip_path = self._recordings_dir / cam / date / filename
        snap_path = clip_path.with_suffix(".jpg")

        if not clip_path.exists():
            # Motion mode pre-roll race — clip isn't on disk yet.
            # The notification fires text-only.
            return None

        # Idempotent: if an extracted snap already exists from a
        # previous run, reuse it.
        if snap_path.exists() and snap_path.stat().st_size > 0:
            return self._rel(snap_path)

        if not shutil.which(self._ffmpeg):
            if not self._warned_missing_ffmpeg:
                log.warning(
                    "snapshot_extractor: ffmpeg not on PATH (looked for %s); "
                    "motion notifications will be text-only",
                    self._ffmpeg,
                )
                self._warned_missing_ffmpeg = True
            return None

        try:
            # -ss before -i seeks fast (input-side). -frames:v 1 takes
            # exactly one frame. -q:v 4 balances size vs quality.
            # -y overwrites in case a partial file was left from a
            # previous crash.
            result = subprocess.run(
                [
                    self._ffmpeg,
                    "-y",
                    "-ss",
                    f"{self._frame_offset:.2f}",
                    "-i",
                    str(clip_path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    str(DEFAULT_JPEG_QUALITY),
                    str(snap_path),
                ],
                check=False,
                capture_output=True,
                timeout=EXTRACT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            log.warning(
                "snapshot_extractor: ffmpeg timed out extracting %s",
                clip_path,
            )
            return None
        except OSError as exc:  # pragma: no cover
            log.warning("snapshot_extractor: ffmpeg invocation failed: %s", exc)
            return None

        if result.returncode != 0:
            log.warning(
                "snapshot_extractor: ffmpeg rc=%d for %s; stderr=%s",
                result.returncode,
                clip_path,
                (result.stderr or b"")[:200].decode("utf-8", errors="replace"),
            )
            try:
                # Don't leave a 0-byte stub on disk.
                if snap_path.exists() and snap_path.stat().st_size == 0:
                    os.unlink(snap_path)
            except OSError:
                pass
            return None

        if not snap_path.exists() or snap_path.stat().st_size == 0:
            return None

        return self._rel(snap_path)

    # ------------------------------------------------------------------

    def _rel(self, path: Path) -> str:
        """Path-string representation suitable for the wire format —
        relative to recordings_dir."""
        try:
            return str(path.relative_to(self._recordings_dir))
        except ValueError:
            return str(path)
