"""
Camera discovery service — finds cameras on the local network via mDNS.

Responsibilities:
- Browse for _rtsp._tcp services using Avahi/dbus
- Detect new cameras -> add to pending list
- Monitor paired cameras -> update online/offline status
- Track camera firmware version from TXT records
- Trigger audit log entries for camera state changes

Camera considered offline after 30 seconds with no mDNS response.

Note: Full mDNS browsing requires Avahi (Linux-only). This module
provides the service interface; actual mDNS browsing runs only on
the RPi hardware.
"""

import threading
from datetime import UTC, datetime

OFFLINE_TIMEOUT = 30  # seconds


class DiscoveryService:
    """Manages camera discovery and status tracking.

    On the RPi, this will use Avahi/dbus for mDNS browsing.
    The service can also accept manual camera registrations
    (e.g., for testing or static IP cameras).
    """

    def __init__(self, store, audit=None):
        self._store = store
        self._audit = audit
        self._lock = threading.Lock()
        self._running = False

    def report_camera(self, camera_id, ip, firmware_version=""):
        """Report a camera as seen (from mDNS or heartbeat).

        Creates a pending camera if unknown, or updates last_seen
        and status for known cameras.
        """
        from monitor.models import Camera

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._lock:
            camera = self._store.get_camera(camera_id)
            if camera is None:
                # New camera discovered — add as pending
                camera = Camera(
                    id=camera_id,
                    ip=ip,
                    status="pending",
                    last_seen=now,
                    firmware_version=firmware_version,
                )
                self._store.save_camera(camera)
                if self._audit:
                    self._audit.log_event(
                        "CAMERA_DISCOVERED",
                        detail=f"new camera {camera_id} at {ip}",
                    )
            else:
                # Known camera — update status
                was_offline = camera.status == "offline"
                camera.ip = ip
                camera.last_seen = now
                if firmware_version:
                    camera.firmware_version = firmware_version
                if camera.status != "pending":
                    camera.status = "online"
                self._store.save_camera(camera)
                if was_offline and self._audit:
                    self._audit.log_event(
                        "CAMERA_ONLINE",
                        detail=f"camera {camera_id} back online at {ip}",
                    )

    def check_offline(self):
        """Mark cameras as offline if not seen recently."""
        now = datetime.now(UTC)
        cameras = self._store.get_cameras()

        for camera in cameras:
            if camera.status not in ("online",):
                continue
            if not camera.last_seen:
                continue

            try:
                last = datetime.fromisoformat(camera.last_seen.replace("Z", "+00:00"))
                elapsed = (now - last).total_seconds()
            except (ValueError, TypeError):
                continue

            if elapsed > OFFLINE_TIMEOUT:
                camera.status = "offline"
                self._store.save_camera(camera)
                if self._audit:
                    self._audit.log_event(
                        "CAMERA_OFFLINE",
                        detail=f"camera {camera.id} offline (last seen {int(elapsed)}s ago)",
                    )

    def get_camera_status(self, camera_id):
        """Get current status info for a camera."""
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return None
        return {
            "id": camera.id,
            "name": camera.name,
            "status": camera.status,
            "ip": camera.ip,
            "last_seen": camera.last_seen,
            "firmware_version": camera.firmware_version,
            "resolution": camera.resolution,
            "fps": camera.fps,
        }
