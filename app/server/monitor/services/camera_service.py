"""
Camera management service — orchestrates camera lifecycle operations.

Centralizes the business logic for camera confirmation, updates, and
removal. Routes call this service instead of directly coordinating
store, streaming, and audit concerns.

Design patterns:
- Constructor Injection (store, streaming, audit)
- Single Responsibility (camera lifecycle only)
- Fail-Silent (audit failures don't break operations)
"""

import logging
from datetime import UTC, datetime

log = logging.getLogger("monitor.camera_service")

VALID_RECORDING_MODES = {"continuous", "off"}
VALID_RESOLUTIONS = {"720p", "1080p"}


class CameraService:
    """Orchestrates camera CRUD operations across store, streaming, and audit.

    Args:
        store: Data persistence layer (Store instance).
        streaming: Video pipeline manager (StreamingService instance or None).
        audit: Security audit logger (AuditLogger instance or None).
    """

    def __init__(self, store, streaming=None, audit=None):
        self._store = store
        self._streaming = streaming
        self._audit = audit

    def add_camera(
        self, camera_id: str, name: str = "", location: str = ""
    ) -> tuple[dict | None, str, int]:
        """Register a new camera as pending.

        Returns (result_dict, error_string, http_status_code).
        """
        camera_id = camera_id.strip()
        if not camera_id:
            return None, "Camera ID is required", 400

        existing = self._store.get_camera(camera_id)
        if existing is not None:
            return None, "Camera already exists", 409

        from monitor.models import Camera

        camera = Camera(
            id=camera_id,
            name=name.strip() or camera_id,
            location=location.strip(),
            status="pending",
        )
        self._store.save_camera(camera)

        log.info("Camera registered: %s", camera_id)

        return (
            {"id": camera.id, "name": camera.name, "status": camera.status},
            "",
            201,
        )

    def list_cameras(self) -> list[dict]:
        """List all cameras (confirmed + pending)."""
        cameras = self._store.get_cameras()
        return [
            {
                "id": c.id,
                "name": c.name,
                "location": c.location,
                "status": c.status,
                "ip": c.ip,
                "recording_mode": c.recording_mode,
                "resolution": c.resolution,
                "fps": c.fps,
                "paired_at": c.paired_at,
                "last_seen": c.last_seen,
                "firmware_version": c.firmware_version,
            }
            for c in cameras
        ]

    def get_camera_status(self, camera_id: str) -> tuple[dict | None, str]:
        """Get live status for a camera.

        Returns (status_dict, error_string). Error is empty on success.
        """
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return None, "Camera not found"

        return {
            "id": camera.id,
            "name": camera.name,
            "status": camera.status,
            "ip": camera.ip,
            "last_seen": camera.last_seen,
            "firmware_version": camera.firmware_version,
            "resolution": camera.resolution,
            "fps": camera.fps,
            "recording_mode": camera.recording_mode,
        }, ""

    def confirm(
        self,
        camera_id: str,
        name: str = "",
        location: str = "",
        user: str = "",
        ip: str = "",
    ) -> tuple[dict | None, str, int]:
        """Confirm a discovered (pending) camera.

        Transitions camera from pending → online, sets RTSP URL,
        starts video pipelines if recording mode is continuous.

        Returns (result_dict, error_string, http_status_code).
        """
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return None, "Camera not found", 404

        if camera.status != "pending":
            return None, "Camera is already confirmed", 400

        camera.name = name or camera.name or camera_id
        camera.location = location or camera.location
        camera.status = "online"
        camera.paired_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        camera.rtsp_url = f"rtsp://127.0.0.1:8554/{camera.id}"

        self._store.save_camera(camera)

        # Start video pipelines
        if self._streaming and camera.recording_mode == "continuous":
            self._streaming.start_camera(camera.id)

        self._log_audit(
            "CAMERA_CONFIRMED",
            user,
            ip,
            f"confirmed camera {camera_id} as '{camera.name}'",
        )

        return (
            {
                "id": camera.id,
                "name": camera.name,
                "status": camera.status,
                "paired_at": camera.paired_at,
            },
            "",
            200,
        )

    def update(
        self, camera_id: str, data: dict, user: str = "", ip: str = ""
    ) -> tuple[str, int]:
        """Update camera settings.

        Validates input, persists changes, and handles recording mode
        transitions (starting/stopping video pipelines as needed).

        Returns (error_string, http_status_code). Empty error = success.
        """
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return "Camera not found", 404

        if not data:
            return "JSON body required", 400

        # Validate fields
        error = self._validate_update(data)
        if error:
            return error, 400

        old_recording_mode = camera.recording_mode

        for key, value in data.items():
            setattr(camera, key, value)

        self._store.save_camera(camera)

        # Handle recording mode transitions
        if self._streaming and "recording_mode" in data:
            if data["recording_mode"] == "continuous" and old_recording_mode == "off":
                self._streaming.start_camera(camera_id)
            elif data["recording_mode"] == "off" and old_recording_mode == "continuous":
                self._streaming.stop_camera(camera_id)

        self._log_audit(
            "CAMERA_UPDATED",
            user,
            ip,
            f"updated camera {camera_id}: {', '.join(sorted(data.keys()))}",
        )

        return "", 200

    def delete(self, camera_id: str, user: str = "", ip: str = "") -> tuple[str, int]:
        """Remove a camera and stop its video pipelines.

        Returns (error_string, http_status_code). Empty error = success.
        """
        # Stop pipelines before deleting
        if self._streaming:
            self._streaming.stop_camera(camera_id)

        deleted = self._store.delete_camera(camera_id)
        if not deleted:
            return "Camera not found", 404

        self._log_audit(
            "CAMERA_DELETED",
            user,
            ip,
            f"removed camera {camera_id}",
        )

        return "", 200

    def _validate_update(self, data: dict) -> str:
        """Validate camera update fields. Returns error string or empty."""
        allowed = {"name", "location", "recording_mode", "resolution", "fps"}
        unknown = set(data.keys()) - allowed
        if unknown:
            return f"Unknown fields: {', '.join(sorted(unknown))}"

        if (
            "recording_mode" in data
            and data["recording_mode"] not in VALID_RECORDING_MODES
        ):
            return (
                f"recording_mode must be one of: "
                f"{', '.join(sorted(VALID_RECORDING_MODES))}"
            )

        if "resolution" in data and data["resolution"] not in VALID_RESOLUTIONS:
            return f"resolution must be one of: {', '.join(sorted(VALID_RESOLUTIONS))}"

        if "fps" in data:
            fps = data["fps"]
            if not isinstance(fps, int) or fps < 1 or fps > 30:
                return "fps must be an integer between 1 and 30"

        if "name" in data:
            name = data["name"]
            if not isinstance(name, str) or len(name) < 1 or len(name) > 64:
                return "name must be 1-64 characters"

        return ""

    def _log_audit(self, event, user, ip, detail):
        """Log audit event, swallowing errors."""
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception as e:
            log.warning("Audit log failed: %s", e)
