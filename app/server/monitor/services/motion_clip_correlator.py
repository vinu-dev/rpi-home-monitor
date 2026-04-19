"""
Motion clip correlator — match motion events to finalised recorded clips.

Given a motion event (camera_id + started_at ISO8601Z), scan the
recordings directory for the single finalised ``.mp4`` whose time range
contains the event and return a ``clip_ref`` dict suitable for
``MotionEventStore.attach_clip(...)``.

"Finalised" means the file extension is exactly ``.mp4`` — files still
being written live under ``.mp4.part`` and are deliberately ignored.
Recorder.rename()-on-close is the counterpart discipline.

See docs/exec-plans/motion-detection.md §Click-through router.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("monitor.motion_clip_correlator")

# Both recorder layouts share this correlator (see recorder_service.py
# module docstring for why two layouts exist on real devices).
_FLAT_STEM_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$")
_DATED_STEM_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})$")

DEFAULT_CLIP_DURATION_SECONDS = 180


def _parse_event_time(iso_ts: str) -> datetime | None:
    """Parse an ISO8601 ``...Z`` string into a UTC-aware datetime."""
    if not iso_ts:
        return None
    try:
        # Python 3.11+ supports "Z" directly via fromisoformat, but the
        # explicit replacement avoids depending on the runtime version.
        normalised = iso_ts.replace("Z", "+00:00")
        return datetime.fromisoformat(normalised).astimezone(UTC)
    except (ValueError, TypeError):
        return None


def _clip_start_from_flat(stem: str) -> datetime | None:
    """Extract UTC start time from ``YYYYMMDD_HHMMSS`` stem."""
    m = _FLAT_STEM_RE.match(stem)
    if not m:
        return None
    y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, hh, mm, ss, tzinfo=UTC)
    except ValueError:
        return None


def _clip_start_from_dated(dir_date: str, stem: str) -> datetime | None:
    """Extract UTC start time from ``YYYY-MM-DD`` dir + ``HH-MM-SS`` stem."""
    m = _DATED_STEM_RE.match(stem)
    if not m:
        return None
    try:
        y, mo, d = (int(x) for x in dir_date.split("-"))
        hh, mm, ss = (int(x) for x in m.groups())
        return datetime(y, mo, d, hh, mm, ss, tzinfo=UTC)
    except ValueError:
        return None


class MotionClipCorrelator:
    """Look up the finalised clip covering a given event timestamp.

    Args:
        recordings_dir: Path to /data/recordings root.
        clip_duration_seconds: Segment length the recorder writes. Used
            to compute whether an event falls inside a given clip's time
            range. Defaults to 180 s (ADR-0017 default).
    """

    def __init__(
        self,
        recordings_dir: str | Path,
        clip_duration_seconds: int = DEFAULT_CLIP_DURATION_SECONDS,
    ):
        self._recordings_dir = Path(recordings_dir)
        self._clip_duration = max(1, int(clip_duration_seconds))

    def set_recordings_dir(self, new_dir: str | Path) -> None:
        """Update the recordings root at runtime.

        Called when the user selects a USB device for storage — the
        StreamingService moves recorder output to the new path; the
        correlator must follow or it'll keep looking in the stale
        /data/recordings default and produce `clip_ref: null` for every
        event (which silently falls the UI through to Live, losing
        saved-clip playback).
        """
        self._recordings_dir = Path(new_dir)

    def set_clip_duration(self, new_duration: int) -> None:
        """Keep the correlator's window in sync with the recorder."""
        self._clip_duration = max(1, int(new_duration))

    def find_clip(self, camera_id: str, event_started_at: str) -> dict | None:
        """Return a clip_ref for the clip covering the event, or None.

        The returned dict matches the shape stored on `MotionEvent.clip_ref`:
            {camera_id, date, filename, offset_seconds}

        Offset is the seconds elapsed into the clip at which the motion
        started. Clamped to [0, clip_duration) — callers don't need to
        worry about negative offsets.
        """
        event_dt = _parse_event_time(event_started_at)
        if event_dt is None:
            log.debug(
                "motion_clip_correlator: unparseable event_started_at=%r",
                event_started_at,
            )
            return None

        cam_dir = self._recordings_dir / camera_id
        if not cam_dir.is_dir():
            return None

        # Two-day window: a clip that started yesterday can still cover
        # an event that happened just after midnight today.
        candidate = self._scan(cam_dir, event_dt)
        if candidate is not None:
            return candidate

        # Cheap explicit second sweep against yesterday's dated subdir
        # (the flat layout is already handled by `_scan`).
        yesterday = (event_dt - timedelta(days=1)).date().isoformat()
        dated_dir = cam_dir / yesterday
        if dated_dir.is_dir():
            match = self._scan_dated(dated_dir, yesterday, event_dt, camera_id)
            if match is not None:
                return match

        return None

    # --- Internals --------------------------------------------------------

    def _scan(self, cam_dir: Path, event_dt: datetime) -> dict | None:
        today = event_dt.date().isoformat()

        # Flat layout: <cam>/YYYYMMDD_HHMMSS.mp4 anywhere under cam_dir.
        # Deliberately filters on ``.mp4`` (not .mp4.part) — only finalised
        # clips are candidates, matching the recorder's rename-on-close
        # discipline.
        for mp4 in cam_dir.glob("*.mp4"):
            if mp4.suffix != ".mp4":
                continue
            start = _clip_start_from_flat(mp4.stem)
            if start is None:
                continue
            match = self._match(
                mp4,
                start,
                event_dt,
                camera_id=cam_dir.name,
                date_str=start.date().isoformat(),
            )
            if match is not None:
                return match

        # Dated layout: <cam>/YYYY-MM-DD/HH-MM-SS.mp4 — check today's dir.
        dated_dir = cam_dir / today
        if dated_dir.is_dir():
            return self._scan_dated(dated_dir, today, event_dt, cam_dir.name)
        return None

    def _scan_dated(
        self,
        dated_dir: Path,
        date_str: str,
        event_dt: datetime,
        camera_id: str,
    ) -> dict | None:
        for mp4 in dated_dir.glob("*.mp4"):
            if mp4.suffix != ".mp4":
                continue
            start = _clip_start_from_dated(date_str, mp4.stem)
            if start is None:
                continue
            match = self._match(mp4, start, event_dt, camera_id, date_str)
            if match is not None:
                return match
        return None

    def _match(
        self,
        mp4: Path,
        clip_start: datetime,
        event_dt: datetime,
        camera_id: str,
        date_str: str,
    ) -> dict | None:
        delta = (event_dt - clip_start).total_seconds()
        if 0 <= delta < self._clip_duration:
            return {
                "camera_id": camera_id,
                "date": date_str,
                "filename": mp4.name,
                "offset_seconds": int(delta),
            }
        return None
