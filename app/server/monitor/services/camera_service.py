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
import re
from datetime import UTC, datetime

log = logging.getLogger("monitor.camera_service")

VALID_RECORDING_MODES = {"off", "continuous", "schedule", "motion"}
VALID_RESOLUTIONS = {"720p", "1080p"}
VALID_SCHEDULE_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")

# Camera IDs must follow cam-<hexsuffix> format: cam- prefix + 1-32 lowercase hex chars.
# This matches the hardware serial format generated on cameras and prevents:
# - Directory traversal via ../
# - Filesystem limit violations (>255 chars)
# - Injection via shell-unsafe characters
_CAMERA_ID_RE = re.compile(r"^cam-[a-z0-9]{1,48}$")

# Stream parameters that should be pushed to the camera (ADR-0015)
STREAM_PARAMS = {
    "width",
    "height",
    "fps",
    "bitrate",
    "h264_profile",
    "keyframe_interval",
    "rotation",
    "hflip",
    "vflip",
    "motion_sensitivity",
    # recording_motion_enabled is named for the RecordingScheduler
    # on the server side, but on the wire it maps to the camera's
    # MOTION_DETECTION flag that gates its on-device detector.
    # See ``_translate_stream_params_for_wire`` below.
    "recording_motion_enabled",
}


def _translate_stream_params_for_wire(params: dict) -> dict:
    """Rename server-side model fields to the keys the camera expects.

    The camera's ``ControlHandler.set_config`` upper-cases each key
    and stores it in ``camera.conf`` as-is. The stream pipeline then
    reads specific keys (``MOTION_DETECTION``, not
    ``RECORDING_MOTION_ENABLED``), so without this translation a
    toggled-on motion flag silently ends up in an unread config
    bucket and the on-device detector stays off.

    Keep this mapping narrow — only fields where the server model
    name diverges from the camera config key. Most params already
    agree by name (``width``, ``bitrate``, etc.) and pass through.
    """
    translated = dict(params)
    if "recording_motion_enabled" in translated:
        translated["motion_detection"] = translated.pop("recording_motion_enabled")
    return translated


def _validate_schedule(schedule) -> str:
    """Validate a recording_schedule payload. Returns error string or ''."""
    if not isinstance(schedule, list):
        return "recording_schedule must be a list"
    for i, item in enumerate(schedule):
        if not isinstance(item, dict):
            return f"recording_schedule[{i}] must be an object"
        if set(item.keys()) != {"days", "start", "end"}:
            return (
                f"recording_schedule[{i}] must have exactly keys 'days', 'start', 'end'"
            )
        days = item["days"]
        if not isinstance(days, list) or not days:
            return f"recording_schedule[{i}].days must be a non-empty list"
        for d in days:
            if d not in VALID_SCHEDULE_DAYS:
                return f"recording_schedule[{i}].days has invalid day: {d!r}"
        for key in ("start", "end"):
            val = item[key]
            if not isinstance(val, str) or not _TIME_RE.match(val):
                return f"recording_schedule[{i}].{key} must match HH:MM"
            hh, mm = val.split(":")
            try:
                h, m = int(hh), int(mm)
            except ValueError:
                return f"recording_schedule[{i}].{key} must be a valid 24h time"
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return f"recording_schedule[{i}].{key} must be a valid 24h time"
    return ""


class CameraService:
    """Orchestrates camera CRUD operations across store, streaming, and audit.

    Args:
        store: Data persistence layer (Store instance).
        streaming: Video pipeline manager (StreamingService instance or None).
        audit: Security audit logger (AuditLogger instance or None).
    """

    def __init__(self, store, streaming=None, audit=None, control_client=None):
        self._store = store
        self._streaming = streaming
        self._audit = audit
        self._control = control_client

    def add_camera(
        self, camera_id: str, name: str = "", location: str = ""
    ) -> tuple[dict | None, str, int]:
        """Register a new camera as pending.

        Returns (result_dict, error_string, http_status_code).
        """
        camera_id = camera_id.strip()
        if not camera_id:
            return None, "Camera ID is required", 400

        # Validate format: cam-<lowercase-hex>, max 52 chars total.
        # Prevents directory traversal, injection, and filesystem limit issues.
        if not _CAMERA_ID_RE.match(camera_id):
            return (
                None,
                "Invalid camera ID format. Must be 'cam-' followed by 1-48 lowercase alphanumeric characters.",
                400,
            )

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

    def list_cameras(self, admin_view: bool = True) -> list[dict]:
        """List all cameras (confirmed + pending).

        admin_view=True: return all fields including network/health details.
        admin_view=False (viewer role): omit fields that could expose network
            topology (ip) or enable occupancy tracking (cpu_temp, memory_percent,
            uptime_seconds). Viewers need camera status to use the UI, but not
            internal health metrics or the camera's LAN address.
        """
        cameras = self._store.get_cameras()
        result = []
        for c in cameras:
            # "streaming" = camera's RTSP ffmpeg is running (self-reported via
            # heartbeat). This is the meaningful question: "is this camera
            # broadcasting?" The server's HLS pipeline is on-demand (only active
            # while Live view is open) so it is NOT the right signal here.
            streaming_now = bool(c.streaming)

            cam = {
                "id": c.id,
                "name": c.name,
                "location": c.location,
                "status": c.status,
                "recording_mode": c.recording_mode,
                "resolution": c.resolution,
                "fps": c.fps,
                "paired_at": c.paired_at,
                "last_seen": c.last_seen,
                "firmware_version": c.firmware_version,
                "width": c.width,
                "height": c.height,
                "bitrate": c.bitrate,
                "h264_profile": c.h264_profile,
                "keyframe_interval": c.keyframe_interval,
                "rotation": c.rotation,
                "hflip": c.hflip,
                "vflip": c.vflip,
                # getattr default = 5 (Medium) so cameras persisted before
                # the motion_sensitivity field was added continue to load
                # with the shipping default.
                "motion_sensitivity": getattr(c, "motion_sensitivity", 5),
                "config_sync": c.config_sync,
                "streaming": streaming_now,
                # ADR-0017 recording-mode fields
                "recording_schedule": list(c.recording_schedule),
                "recording_motion_enabled": c.recording_motion_enabled,
                "desired_stream_state": c.desired_stream_state,
                # Hardware health is not admin-gated — even viewers
                # benefit from seeing "no camera module detected" on
                # the dashboard so they don't wait for a broken
                # stream to come up. ``getattr`` with defaults keeps
                # test stubs (SimpleNamespace) working without having
                # to enumerate every Camera field.
                "hardware_ok": getattr(c, "hardware_ok", True),
                "hardware_error": getattr(c, "hardware_error", ""),
                # Structured faults (ADR-0023). Kept alongside the
                # flat legacy fields until all consumers migrate.
                "hardware_faults": list(getattr(c, "hardware_faults", []) or []),
            }
            if admin_view:
                # Admin-only fields: network topology + health metrics
                cam["ip"] = c.ip
                cam["cpu_temp"] = c.cpu_temp
                cam["memory_percent"] = c.memory_percent
                cam["uptime_seconds"] = c.uptime_seconds
            result.append(cam)
        return result

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
            "width": camera.width,
            "height": camera.height,
            "bitrate": camera.bitrate,
            "h264_profile": camera.h264_profile,
            "keyframe_interval": camera.keyframe_interval,
            "rotation": camera.rotation,
            "hflip": camera.hflip,
            "vflip": camera.vflip,
            "motion_sensitivity": getattr(camera, "motion_sensitivity", 5),
            "config_sync": camera.config_sync,
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
            return self._confirmed_result(camera), "", 200

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

        return self._confirmed_result(camera), "", 200

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

        # Push stream params to camera if any changed (ADR-0015).
        # Translate server-side names to the camera's wire keys
        # (e.g. recording_motion_enabled → motion_detection).
        stream_changes = {k: v for k, v in data.items() if k in STREAM_PARAMS}
        wire_changes = _translate_stream_params_for_wire(stream_changes)
        if wire_changes and camera.ip and self._control:
            result, err = self._control.set_config(camera.ip, wire_changes)
            if err:
                log.warning("Failed to push config to camera %s: %s", camera_id, err)
                camera.config_sync = "pending"
            else:
                camera.config_sync = "synced"
        elif stream_changes and not camera.ip:
            camera.config_sync = "pending"

        self._store.save_camera(camera)

        # ADR-0017: recording-mode transitions are reconciled by
        # RecordingScheduler on its next tick — no direct pipeline calls here.
        _ = old_recording_mode  # retained for audit/logging compatibility

        self._log_audit(
            "CAMERA_UPDATED",
            user,
            ip,
            f"updated camera {camera_id}: {', '.join(sorted(data.keys()))}",
        )

        return "", 200

    def accept_heartbeat(self, camera_id: str, data: dict) -> tuple[dict, str, int]:
        """Accept a heartbeat from a camera and update its live status.

        Updates last_seen, status, streaming flag, and health metrics.
        If the camera's config_sync is 'pending', returns the stored
        stream config so the camera can re-apply it.

        Returns (response_dict, error_string, http_status_code).
        """
        camera = self._store.get_camera(camera_id)
        if not camera:
            return {}, "Camera not found", 404

        was_offline = camera.status == "offline"
        camera.status = "online"
        camera.last_seen = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Update live health fields
        camera.streaming = bool(data.get("streaming", False))
        if "cpu_temp" in data:
            try:
                camera.cpu_temp = float(data["cpu_temp"])
            except (TypeError, ValueError):
                pass
        if "memory_percent" in data:
            try:
                camera.memory_percent = int(data["memory_percent"])
            except (TypeError, ValueError):
                pass
        if "uptime_seconds" in data:
            try:
                camera.uptime_seconds = int(data["uptime_seconds"])
            except (TypeError, ValueError):
                pass
        # Hardware health — "no camera module detected" + friends.
        # Accept only the expected types; ignore garbage.
        if "hardware_ok" in data:
            camera.hardware_ok = bool(data["hardware_ok"])
        if "hardware_error" in data and isinstance(data["hardware_error"], str):
            # Clip to a sane length so a malformed camera can't bloat
            # the persisted store.
            camera.hardware_error = data["hardware_error"][:512]
        # Structured fault list (ADR-0023). Each entry is a dict with
        # code/severity/message/hint/context. We store as-is with two
        # sanity caps: list length and message length. If a future
        # camera emits garbage (non-dict entries, missing code), we
        # drop just those entries rather than the whole payload.
        if "hardware_faults" in data and isinstance(data["hardware_faults"], list):
            accepted: list[dict] = []
            for raw in data["hardware_faults"][:32]:
                if not isinstance(raw, dict):
                    continue
                code = raw.get("code")
                if not isinstance(code, str) or not code:
                    continue
                accepted.append(
                    {
                        "code": code[:64],
                        "severity": str(raw.get("severity", "warning"))[:16],
                        "message": str(raw.get("message", ""))[:80],
                        "hint": str(raw.get("hint", ""))[:512],
                        "context": (
                            raw.get("context", {})
                            if isinstance(raw.get("context", {}), dict)
                            else {}
                        ),
                    }
                )
            camera.hardware_faults = accepted
        # Pick up the camera's post-OTA firmware version the first time
        # it reports in after a reboot. Heartbeat is the most reliable
        # channel — avahi TXT records refresh with noticeable lag and
        # the control /status endpoint needs mTLS that might not be up
        # for a few seconds after boot.
        fw = data.get("firmware_version")
        if fw and isinstance(fw, str):
            camera.firmware_version = fw

        # Capture sync state before touching stream params.
        # If config_sync is "pending" the server has unsent changes — keep them
        # and tell the camera via pending_config instead of overwriting with its
        # potentially-stale values.
        had_pending = camera.config_sync == "pending"

        if (
            "stream_config" in data
            and isinstance(data["stream_config"], dict)
            and not had_pending
        ):
            sc = data["stream_config"]
            for key in sc:
                if key in STREAM_PARAMS:
                    setattr(camera, key, sc[key])
            camera.config_sync = "synced"

        self._store.save_camera(camera)

        if was_offline:
            self._log_audit(
                "CAMERA_ONLINE",
                "camera",
                "",
                f"camera {camera_id} reconnected via heartbeat",
            )

        # If we have a pending config push, include it in the response
        response: dict = {"ok": True}
        if had_pending:
            response["pending_config"] = {
                "width": camera.width,
                "height": camera.height,
                "fps": camera.fps,
                "bitrate": camera.bitrate,
                "h264_profile": camera.h264_profile,
                "keyframe_interval": camera.keyframe_interval,
                "rotation": camera.rotation,
                "hflip": camera.hflip,
                "vflip": camera.vflip,
            }

        return response, "", 200

    def accept_camera_config(
        self, camera_id: str, stream_config: dict
    ) -> tuple[str, int]:
        """Accept a config notification from the camera (source of truth).

        Updates stored config without pushing back to camera.
        Returns (error_string, http_status_code).
        """
        camera = self._store.get_camera(camera_id)
        if not camera:
            return "Camera not found", 404

        # Only accept known stream params
        for key in stream_config:
            if key not in STREAM_PARAMS:
                return f"Unknown parameter: {key}", 400

        for key, value in stream_config.items():
            setattr(camera, key, value)

        camera.config_sync = "synced"
        self._store.save_camera(camera)

        self._log_audit(
            "CAMERA_CONFIG_RECEIVED",
            "camera",
            "",
            f"config notification from {camera_id}: "
            f"{', '.join(f'{k}={v}' for k, v in stream_config.items())}",
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
        allowed = {
            "name",
            "location",
            "recording_mode",
            "recording_schedule",
            "recording_motion_enabled",
            "resolution",
            "fps",
            "width",
            "height",
            "bitrate",
            "h264_profile",
            "keyframe_interval",
            "rotation",
            "hflip",
            "vflip",
            "motion_sensitivity",
        }
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

        if "width" in data and (
            not isinstance(data["width"], int) or data["width"] < 1
        ):
            return "width must be a positive integer"

        if "height" in data and (
            not isinstance(data["height"], int) or data["height"] < 1
        ):
            return "height must be a positive integer"

        if "bitrate" in data:
            br = data["bitrate"]
            if not isinstance(br, int) or br < 500000 or br > 8000000:
                return "bitrate must be between 500000 and 8000000"

        if "keyframe_interval" in data:
            ki = data["keyframe_interval"]
            if not isinstance(ki, int) or ki < 1 or ki > 120:
                return "keyframe_interval must be between 1 and 120"

        if "h264_profile" in data and data["h264_profile"] not in (
            "baseline",
            "main",
            "high",
        ):
            return "h264_profile must be one of: baseline, main, high"

        if "rotation" in data and data["rotation"] not in (0, 180):
            return "rotation must be 0 or 180"

        if "hflip" in data and not isinstance(data["hflip"], bool):
            return "hflip must be a boolean"

        if "vflip" in data and not isinstance(data["vflip"], bool):
            return "vflip must be a boolean"

        if "motion_sensitivity" in data:
            ms = data["motion_sensitivity"]
            if not isinstance(ms, int) or ms < 1 or ms > 10:
                return "motion_sensitivity must be an integer between 1 and 10"

        if "recording_schedule" in data:
            err = _validate_schedule(data["recording_schedule"])
            if err:
                return err

        if "recording_motion_enabled" in data and not isinstance(
            data["recording_motion_enabled"], bool
        ):
            return "recording_motion_enabled must be a boolean"

        return ""

    @staticmethod
    def _confirmed_result(camera) -> dict:
        """Serialize the minimal dashboard payload for a confirmed camera."""
        return {
            "id": camera.id,
            "name": camera.name or camera.id,
            "status": camera.status,
            "paired_at": camera.paired_at,
        }

    def _log_audit(self, event, user, ip, detail):
        """Log audit event, swallowing errors."""
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception as e:
            log.warning("Audit log failed: %s", e)
