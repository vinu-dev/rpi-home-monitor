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
from dataclasses import asdict
from pathlib import Path

from monitor.services.recorder_service import RecorderService

log = logging.getLogger("monitor.recordings-service")


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

    def list_clips(self, camera_id: str, date: str = ""):
        """List clips for a camera on a date.

        Returns:
            (list[dict], None, 200) on success.
            (None, error_message, status_code) on failure.
        """
        camera = self._store.get_camera(camera_id)
        if camera is None:
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
        camera = self._store.get_camera(camera_id)
        if camera is None:
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
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return None, "Camera not found", 404

        recorder = self._get_recorder()
        clip = recorder.get_latest_clip(camera_id)
        if clip is None:
            return None, "No recordings found", 404

        return asdict(clip), None, 200

    def resolve_clip_path(self, camera_id: str, date: str, filename: str):
        """Resolve the full path to a clip file.

        Returns:
            (Path, None, 200) if file exists.
            (None, error_message, status_code) on failure.
        """
        if not filename.endswith(".mp4"):
            return None, "Invalid filename", 400

        recorder = self._get_recorder()
        clip_path = Path(recorder._recordings_dir) / camera_id / date / filename
        if not clip_path.is_file():
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
