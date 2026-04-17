"""
Recordings service — orchestrates clip queries and deletion.

Thin service layer over RecorderService that adds:
- Camera existence validation (via store)
- Audit logging for destructive actions
- Storage-aware recordings directory resolution

Routes in api/recordings.py delegate here; they never touch
the store or audit logger directly.
"""

import logging
import re
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from monitor.services.recorder_service import RecorderService

log = logging.getLogger("monitor.recordings-service")

# Clips whose mtime is this fresh are assumed to be the ffmpeg segment
# currently being written. Their mp4 has no `moov` atom yet so the
# browser can't play them — hiding them from the dashboard feed avoids
# the "click → 0:00, blank player" bug. The loop recorder rotates every
# ~180s, so a 10s window is plenty of slack without hiding anything
# that's actually finalised.
_ACTIVE_WRITE_SECONDS = 10.0

# Camera IDs are derived from hardware serials (hex/alnum). Restrict here so
# they cannot encode path traversal when used in filesystem paths.
_CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Recording date directories are always YYYY-MM-DD.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The dashboard has seen two on-disk layouts during its history:
#   dated subdir: <cam>/YYYY-MM-DD/HH-MM-SS.mp4       (legacy recorder)
#   flat:         <cam>/YYYYMMDD_HHMMSS.mp4           (loop recorder)
# Both are still present on deployed hardware, so readers must cope with
# either. Writers only ever produce the flat layout today.
_FLAT_STEM_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$")
_DATED_STEM_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})$")


def _parse_clip_date_time(mp4: Path) -> tuple[str, str]:
    """Extract (YYYY-MM-DD, HH:MM:SS) from a clip path.

    Returns ("", "") if the path does not match either known layout —
    callers should skip such entries rather than emit NaN to the UI.
    """
    stem = mp4.stem
    m = _FLAT_STEM_RE.match(stem)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        return f"{y}-{mo}-{d}", f"{hh}:{mm}:{ss}"
    parent = mp4.parent.name
    if _DATE_RE.match(parent):
        m = _DATED_STEM_RE.match(stem)
        if m:
            return parent, stem.replace("-", ":")
    return "", ""


class RecordingsService:
    """Business logic for recording queries and management.

    Args:
        storage_manager: StorageManager (provides recordings_dir).
        store: Store for camera existence checks.
        audit: AuditLogger (optional, for delete events).
        live_dir: Path to HLS live segments directory.
        default_recordings_dir: Fallback if storage_manager is None.
    """

    def __init__(
        self,
        storage_manager,
        store,
        audit=None,
        live_dir="",
        default_recordings_dir="",
    ):
        self._storage_manager = storage_manager
        self._store = store
        self._audit = audit
        self._live_dir = live_dir
        self._default_recordings_dir = default_recordings_dir

    def _get_recorder(self) -> RecorderService:
        """Build a RecorderService using the current recordings directory."""
        if self._storage_manager:
            recordings_dir = self._storage_manager.recordings_dir
        else:
            recordings_dir = self._default_recordings_dir
        return RecorderService(recordings_dir, self._live_dir)

    @property
    def default_recordings_dir(self) -> str:
        """Public accessor used by the system-summary aggregator."""
        return self._default_recordings_dir

    def _recordings_root(self) -> Path:
        """Absolute path to the recordings root (one subdir per camera)."""
        return Path(self._get_recorder()._recordings_dir)

    def _valid_camera_id(self, camera_id: str) -> bool:
        return bool(camera_id) and bool(_CAMERA_ID_RE.match(camera_id))

    def _has_recordings_on_disk(self, camera_id: str) -> bool:
        """True if the camera has any mp4 clips on disk."""
        if not self._valid_camera_id(camera_id):
            return False
        cam_dir = self._recordings_root() / camera_id
        if not cam_dir.is_dir():
            return False
        try:
            return any(d.is_dir() and any(d.glob("*.mp4")) for d in cam_dir.iterdir())
        except OSError:
            return False

    def _camera_known(self, camera_id: str) -> bool:
        """A camera is 'known' to Recordings if it's paired OR has files.
        The latter is the orphan case: admin deleted the Camera record but
        we keep the clips until someone explicitly removes them.
        """
        if self._store.get_camera(camera_id) is not None:
            return True
        return self._has_recordings_on_disk(camera_id)

    def _log_audit(self, event, **kwargs):
        """Log an audit event (fail-silent)."""
        if not self._audit:
            return
        try:
            self._audit.log_event(event, **kwargs)
        except Exception:
            log.debug("Audit log failed for %s", event)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_camera_sources(self):
        """Cameras the Recordings tab can browse: paired + orphans.

        Paired cameras with ``status=pending`` are excluded — they've
        never come online so there are no clips to browse. Orphans
        (directories on disk with mp4s but no matching Camera record)
        appear with ``status=removed`` so the UI can group them under
        a "Removed" archive in the dropdown.

        Returns:
            (list[dict], None, 200). Each dict: {id, name, status}.
            status ∈ {"online", "offline", "removed"}.
        """
        paired = {c.id: c for c in self._store.get_cameras()}
        result = []
        for cam in paired.values():
            if cam.status == "pending":
                continue
            status = "online" if cam.status == "online" else "offline"
            result.append(
                {
                    "id": cam.id,
                    "name": cam.name or cam.id,
                    "status": status,
                }
            )

        root = self._recordings_root()
        if root.is_dir():
            try:
                children = sorted(root.iterdir())
            except OSError:
                children = []
            for child in children:
                if not child.is_dir():
                    continue
                if child.name in paired:
                    continue
                if not self._valid_camera_id(child.name):
                    continue
                if not self._has_recordings_on_disk(child.name):
                    continue
                result.append(
                    {
                        "id": child.name,
                        "name": child.name,
                        "status": "removed",
                    }
                )
        return result, None, 200

    def list_clips(self, camera_id: str, date: str = ""):
        """List clips for a camera on a date.

        Returns:
            (list[dict], None, 200) on success.
            (None, error_message, status_code) on failure.
        """
        if not self._camera_known(camera_id):
            return None, "Camera not found", 404

        recorder = self._get_recorder()
        clips = recorder.list_clips(camera_id, date)
        return [asdict(c) for c in clips], None, 200

    def list_dates(self, camera_id: str):
        """List dates that have recordings for a camera.

        Returns:
            (dict, None, 200) on success.
            (None, error_message, 404) if camera not found.
        """
        if not self._camera_known(camera_id):
            return None, "Camera not found", 404

        recorder = self._get_recorder()
        dates = recorder.get_dates_with_clips(camera_id)
        return {"camera_id": camera_id, "dates": dates}, None, 200

    def latest_clip(self, camera_id: str):
        """Get the most recent clip for a camera.

        Returns:
            (dict, None, 200) on success.
            (None, error_message, status_code) on failure.
        """
        if not self._camera_known(camera_id):
            return None, "Camera not found", 404

        recorder = self._get_recorder()
        clip = recorder.get_latest_clip(camera_id)
        if clip is None:
            return None, "No recordings found", 404

        return asdict(clip), None, 200

    def latest_across_cameras(self):
        """Return the newest clip across every camera (or orphaned archive).

        ADR-0018 Tier-2 dashboard tile. Returns the same shape as
        :meth:`latest_clip` plus a ``camera_name`` field (looked up from
        the Store; orphans fall back to the camera_id).

        Returns:
            (dict, None, 200)  — newest clip, including ``camera_name``.
            (None, msg, 404)   — no clips anywhere.
        """
        root = self._recordings_root()
        if not root.is_dir():
            return None, "No recordings found", 404

        # Same active-write guard as recent_across_cameras: ignore the
        # clip ffmpeg is still writing, otherwise the "Last activity"
        # tile deep-links to an unplayable 0:00 file.
        now = time.time()
        newest_mtime = -1.0
        newest_path: Path | None = None
        newest_cam = ""
        try:
            for cam_dir in root.iterdir():
                if not cam_dir.is_dir():
                    continue
                if not self._valid_camera_id(cam_dir.name):
                    continue
                for mp4 in cam_dir.rglob("*.mp4"):
                    try:
                        mtime = mp4.stat().st_mtime
                    except OSError:
                        continue
                    if (now - mtime) < _ACTIVE_WRITE_SECONDS:
                        continue
                    if mtime > newest_mtime:
                        newest_mtime = mtime
                        newest_path = mp4
                        newest_cam = cam_dir.name
        except OSError:
            return None, "No recordings found", 404

        if newest_path is None:
            return None, "No recordings found", 404

        # Build the response from the walk we already did — the recorder's
        # get_latest_clip() only knows the dated-subdir layout, so falling
        # back to it would miss flat-layout clips written by the loop
        # recorder. We already have the newest path; parse it directly.
        date_str, start_time = _parse_clip_date_time(newest_path)
        if not date_str:
            return None, "No recordings found", 404
        try:
            size = newest_path.stat().st_size
        except OSError:
            size = 0
        data = {
            "camera_id": newest_cam,
            "camera_name": self._camera_display_name(newest_cam),
            "filename": newest_path.name,
            "date": date_str,
            "start_time": start_time,
            "duration_seconds": 180,
            "size_bytes": size,
            "thumbnail": f"{newest_path.stem}.thumb.jpg",
        }
        return data, None, 200

    def recent_across_cameras(self, limit: int = 10):
        """List the most recent N clips across every camera.

        Used by ADR-0018 Tier-3 "Recent events" feed. Returns clips
        reverse-chronologically, each row carrying ``camera_name`` for
        display.

        Args:
            limit: Maximum rows to return (1..50). Clamped to range.

        Returns:
            (list[dict], None, 200)  — zero or more rows.
        """
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))

        root = self._recordings_root()
        if not root.is_dir():
            return [], None, 200

        # Walk once, collect (mtime, cam_id, Path) tuples, then sort + take top N.
        # Skip files whose mtime is still advancing (ffmpeg is writing the
        # current segment) — their `moov` atom hasn't been flushed, so the
        # browser shows a blank 0:00 player when clicked.
        now = time.time()
        entries: list[tuple[float, str, Path]] = []
        try:
            for cam_dir in root.iterdir():
                if not cam_dir.is_dir() or not self._valid_camera_id(cam_dir.name):
                    continue
                for mp4 in cam_dir.rglob("*.mp4"):
                    try:
                        mtime = mp4.stat().st_mtime
                    except OSError:
                        continue
                    if (now - mtime) < _ACTIVE_WRITE_SECONDS:
                        continue
                    entries.append((mtime, cam_dir.name, mp4))
        except OSError:
            return [], None, 200

        entries.sort(reverse=True, key=lambda t: t[0])
        out: list[dict] = []
        for _mtime, cam_id, mp4 in entries[:limit]:
            try:
                size = mp4.stat().st_size
            except OSError:
                size = 0
            date_str, start_time = _parse_clip_date_time(mp4)
            if not date_str:
                # Unrecognised on-disk naming — skip rather than return NaN.
                continue
            thumb_name = f"{mp4.stem}.thumb.jpg"
            out.append(
                {
                    "camera_id": cam_id,
                    "camera_name": self._camera_display_name(cam_id),
                    "filename": mp4.name,
                    "date": date_str,
                    "start_time": start_time,
                    "duration_seconds": 180,
                    "size_bytes": size,
                    "thumbnail": thumb_name,
                }
            )
        return out, None, 200

    def _camera_display_name(self, camera_id: str) -> str:
        """Resolve a human-readable camera name; falls back to the id."""
        try:
            cam = self._store.get_camera(camera_id)
        except Exception:
            cam = None
        if cam is None:
            return camera_id
        return getattr(cam, "name", "") or camera_id

    def resolve_clip_path(self, camera_id: str, date: str, filename: str):
        """Resolve the full path to a clip file.

        Returns:
            (Path, None, 200) if file exists.
            (None, error_message, status_code) on failure.
        """
        if not filename.endswith(".mp4"):
            return None, "Invalid filename", 400

        recorder = self._get_recorder()
        try:
            recordings_root = Path(recorder._recordings_dir).resolve()
            clip_path = (recordings_root / camera_id / date / filename).resolve()
        except (ValueError, OSError):
            # ValueError: embedded null bytes or other invalid path characters.
            # OSError: path resolution failure on some platforms.
            return None, "Invalid path", 400

        # Guard against path traversal: clip must be inside recordings_root.
        # This catches inputs like camera_id="../../etc", date="../..", etc.
        try:
            clip_path.relative_to(recordings_root)
        except ValueError:
            return None, "Invalid path", 400

        if not clip_path.is_file():
            # Flat-layout fallback: loop recorder writes clips directly under
            # <recordings_root>/<camera_id>/ with YYYYMMDD_HHMMSS.mp4 stems.
            # The UI still addresses them with the dated URL shape
            # /<cam>/<YYYY-MM-DD>/<filename>, so check the flat location here
            # before giving up.
            try:
                flat_path = (recordings_root / camera_id / filename).resolve()
                flat_path.relative_to(recordings_root)
            except (ValueError, OSError):
                return None, "Clip not found", 404
            if flat_path.is_file():
                return flat_path, None, 200
            return None, "Clip not found", 404

        return clip_path, None, 200

    def delete_clip(
        self,
        camera_id: str,
        date: str,
        filename: str,
        requesting_user: str = "",
        requesting_ip: str = "",
    ):
        """Delete a clip and log the action.

        Returns:
            (dict, None, 200) on success.
            (None, error_message, status_code) on failure.
        """
        if not filename.endswith(".mp4"):
            return None, "Invalid filename", 400

        recorder = self._get_recorder()
        deleted = recorder.delete_clip(camera_id, date, filename)
        if not deleted:
            return None, "Clip not found", 404

        self._log_audit(
            "CLIP_DELETED",
            user=requesting_user,
            ip=requesting_ip,
            detail=f"deleted {camera_id}/{date}/{filename}",
        )

        return {"message": "Clip deleted"}, None, 200

    # ------------------------------------------------------------------
    # Bulk deletion — used by the Recordings tab's danger zone and
    # multi-select toolbar. Paths are validated + resolved + traversal-
    # checked before any rmtree. Orphan cameras are deletable too; that
    # is the main way to clean up a removed-camera archive.
    # ------------------------------------------------------------------

    def _safe_subpath(self, *parts: str):
        """Resolve a path under the recordings root. Returns (path, err).

        ``err`` is "" on success. Any path that escapes the root, or
        components that fail validation, return an error + None path.
        """
        root = self._recordings_root().resolve()
        try:
            target = (root.joinpath(*parts)).resolve()
        except (ValueError, OSError):
            return None, "Invalid path"
        try:
            target.relative_to(root)
        except ValueError:
            return None, "Invalid path"
        return target, ""

    def delete_date(
        self,
        camera_id: str,
        date: str,
        requesting_user: str = "",
        requesting_ip: str = "",
    ):
        """Delete every clip for one camera on one date.

        Returns (dict, None, 200) on success with a ``count`` of deleted
        clips, else (None, error_message, status).
        """
        if not self._valid_camera_id(camera_id):
            return None, "Invalid camera id", 400
        if not _DATE_RE.match(date or ""):
            return None, "Invalid date", 400
        if not self._camera_known(camera_id):
            return None, "Camera not found", 404

        target, err = self._safe_subpath(camera_id, date)
        if err:
            return None, err, 400
        if not target.is_dir():
            return None, "No recordings on that date", 404

        count = sum(1 for _ in target.glob("*.mp4"))
        shutil.rmtree(target, ignore_errors=False)

        self._log_audit(
            "CLIPS_DELETED",
            user=requesting_user,
            ip=requesting_ip,
            detail=f"deleted {count} clips from {camera_id}/{date}",
        )
        return {"message": f"Deleted {count} clips", "count": count}, None, 200

    def delete_camera_recordings(
        self,
        camera_id: str,
        requesting_user: str = "",
        requesting_ip: str = "",
    ):
        """Delete every clip for one camera across all dates.

        The Camera record itself (if any) is left alone — unpair/remove
        is a separate operation. Only the recordings directory is purged.
        """
        if not self._valid_camera_id(camera_id):
            return None, "Invalid camera id", 400
        if not self._camera_known(camera_id):
            return None, "Camera not found", 404

        target, err = self._safe_subpath(camera_id)
        if err:
            return None, err, 400
        if not target.is_dir():
            return None, "No recordings", 404

        count = sum(1 for _ in target.rglob("*.mp4"))
        shutil.rmtree(target, ignore_errors=False)

        self._log_audit(
            "CLIPS_DELETED",
            user=requesting_user,
            ip=requesting_ip,
            detail=f"deleted all {count} clips for {camera_id}",
        )
        return {"message": f"Deleted {count} clips", "count": count}, None, 200
