"""
Storage management service — orchestrates USB storage operations.

Centralizes the business logic for USB device selection, formatting,
ejection, and storage status. Routes call this service instead of
directly coordinating USB, storage manager, store, and audit concerns.

Design patterns:
- Constructor Injection (storage_manager, store, audit)
- Single Responsibility (storage operations only)
- Fail-Silent (audit failures don't break operations)
"""

import logging
import os
from pathlib import Path

from monitor.services import privileged, usb

log = logging.getLogger("monitor.storage_service")


# REQ: SWR-028; RISK: RISK-013; SEC: SC-013; TEST: TC-025
class StorageService:
    """Orchestrates USB storage operations across manager, store, and audit.

    Args:
        storage_manager: StorageManager instance for dir switching and stats.
        store: Data persistence layer (Store instance).
        audit: Security audit logger (AuditLogger instance or None).
        default_recordings_dir: Fallback internal recording path.
    """

    def __init__(
        self,
        storage_manager,
        store,
        audit=None,
        default_recordings_dir="/data/recordings",
    ):
        self._storage_manager = storage_manager
        self._store = store
        self._audit = audit
        self._default_dir = default_recordings_dir

    def get_status(self) -> tuple[dict | None, str]:
        """Return current storage stats.

        Returns (stats_dict, error_string). Error is empty on success.
        """
        if not self._storage_manager:
            return None, "Storage manager not initialized"
        try:
            stats = self._storage_manager.get_storage_stats()
        except OSError as exc:
            log.warning("Storage status unavailable: %s", exc)
            stats = self._unavailable_stats(str(exc))
        return stats, ""

    def list_devices(self) -> list[dict]:
        """List available USB block devices.

        Each device carries an ``in_use`` flag so the UI can render an
        "In use" state instead of a clickable Use button for whichever
        device is currently backing recordings. The flag is computed by
        matching the device's mount point against the storage manager's
        active recordings directory.
        """
        devices = usb.detect_devices()
        rec_dir = self._active_recordings_dir()
        configured_device = self._configured_usb_device()
        configured_uuid = self._configured_usb_uuid()
        configured_present = any(d.get("path") == configured_device for d in devices)
        legacy_single_candidate = (
            bool(configured_device)
            and not configured_uuid
            and not configured_present
            and len(devices) == 1
            and bool(devices[0].get("supported"))
        )
        for d in devices:
            is_active = bool(rec_dir) and self._device_backs_dir(d, rec_dir)
            is_configured = (
                (bool(configured_device) and d.get("path") == configured_device)
                or (bool(configured_uuid) and d.get("uuid") == configured_uuid)
                or legacy_single_candidate
            )
            d["in_use"] = is_active
            d["configured"] = is_configured
            d["configured_inactive"] = is_configured and not is_active
            d["configured_path_changed"] = (
                is_configured
                and bool(configured_device)
                and d.get("path") != configured_device
            )
        return devices

    def _unavailable_stats(self, error: str) -> dict:
        rec_dir = self._active_recordings_dir() or self._default_dir
        return {
            "total_gb": 0,
            "used_gb": 0,
            "free_gb": 0,
            "percent": 0.0,
            "recordings_mb": 0,
            "camera_count": 0,
            "clip_count": 0,
            "per_camera": {},
            "recordings_dir": rec_dir,
            "is_usb": not rec_dir.startswith("/data"),
            "reserve_mb": 0,
            "threshold_percent": None,
            "oldest_segment": None,
            "newest_segment": None,
            "storage_health": "unavailable",
            "storage_error": _storage_error_message(error),
        }

    def _active_recordings_dir(self) -> str:
        if not self._storage_manager:
            return ""
        rec_dir = getattr(self._storage_manager, "recordings_dir", "")
        if not isinstance(rec_dir, str) or not rec_dir:
            return ""
        return rec_dir

    def _configured_usb_device(self) -> str:
        try:
            settings = self._store.get_settings()
        except Exception:
            return ""
        value = getattr(settings, "usb_device", "")
        return value if isinstance(value, str) else ""

    def _configured_usb_uuid(self) -> str:
        try:
            settings = self._store.get_settings()
        except Exception:
            return ""
        value = getattr(settings, "usb_uuid", "")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _device_backs_dir(device: dict, rec_dir: str) -> bool:
        # A device "backs" the recordings dir when its mountpoint is a
        # parent of rec_dir (or equal to it). Parent-prefix (not suffix-
        # strip) is the right check because the on-disk layout under
        # the mount is free-form — e.g. /mnt/recordings/home-monitor-
        # recordings, not always /mnt/recordings/recordings. Comparing
        # only the suffix produced false negatives that left the "Use"
        # button clickable on the active device.
        mp = device.get("mountpoint") or ""
        if not mp:
            return False
        if rec_dir == mp:
            return True
        return rec_dir.startswith(mp.rstrip("/") + "/")

    def select_device(
        self, device_path: str, user: str = "", ip: str = ""
    ) -> tuple[dict | None, str, int]:
        """Select a USB device for recordings.

        Validates the device, mounts it, creates the recordings folder,
        updates the storage manager, and persists the selection.

        Returns (result_dict, error_string, http_status_code).
        """
        if not device_path:
            return None, "device_path required", 400

        # Find the device
        devices = usb.detect_devices()
        device = next((d for d in devices if d["path"] == device_path), None)
        if not device:
            return None, f"Device {device_path} not found", 404

        # Check filesystem
        if not device["supported"]:
            if not device.get("fstype"):
                return (
                    {
                        "needs_format": False,
                        "filesystem_status": device.get("filesystem_status", "unknown"),
                        "fstype": "",
                    },
                    (
                        "Filesystem could not be identified. "
                        "Rescan the USB device or unplug and reconnect it before "
                        "selecting it for recordings."
                    ),
                    409,
                )
            return (
                {
                    "needs_format": True,
                    "filesystem_status": device.get("filesystem_status", "unsupported"),
                    "fstype": device["fstype"],
                },
                (
                    f"Filesystem '{device['fstype']}' not supported. "
                    f"Format the device first via POST /storage/format."
                ),
                400,
            )

        # Mount
        ok, err = usb.mount_device(device_path)
        if not ok:
            return None, f"Failed to mount: {err}", 500

        # Create recordings folder
        try:
            rec_dir = usb.prepare_recordings_dir()
        except OSError as exc:
            log.exception("Failed to prepare USB recordings directory")
            return (
                None,
                "USB mounted, but the recordings folder could not be prepared. "
                f"Check permissions on {usb.DEFAULT_MOUNT_POINT}: {exc}",
                500,
            )

        ok, probe_error = self.verify_recordings_dir(rec_dir)
        if not ok:
            active_dir = self._active_recordings_dir() or self._default_dir
            self._log_audit(
                "USB_STORAGE_REJECTED",
                user,
                ip,
                f"device={device_path}, path={rec_dir}, error={probe_error}",
            )
            return (
                {
                    "recordings_dir": active_dir,
                    "filesystem_status": device.get("filesystem_status", "unknown"),
                },
                (
                    "USB mounted, but the recordings folder failed a write test. "
                    "Recording remains on internal storage. Eject the USB drive "
                    "or reformat/replace it before using it for recordings. "
                    f"Detail: {probe_error}"
                ),
                409,
            )

        # Switch storage manager
        if self._storage_manager:
            self._storage_manager.set_recordings_dir(rec_dir)

        # Persist config
        self._save_usb_config(
            device_path,
            rec_dir,
            uuid=device.get("uuid", ""),
            label=device.get("label", ""),
        )

        self._log_audit(
            "USB_STORAGE_SELECTED",
            user,
            ip,
            f"device={device_path}, mount={usb.DEFAULT_MOUNT_POINT}",
        )

        return (
            {
                "message": (
                    f"USB storage active: {device['model']} ({device['size']})"
                ),
                "recordings_dir": rec_dir,
                "device": device,
            },
            "",
            200,
        )

    def format_device(
        self, device_path: str, confirm: bool = False, user: str = "", ip: str = ""
    ) -> tuple[str, int]:
        """Format a USB device to ext4.

        Returns (error_or_message_string, http_status_code).
        """
        if not device_path:
            return "device_path required", 400

        if not confirm:
            return (
                "Format requires confirm=true. "
                "WARNING: This will ERASE ALL DATA on the device."
            ), 400

        # Verify it's a USB device
        devices = usb.detect_devices()
        device = next((d for d in devices if d["path"] == device_path), None)
        if not device:
            return f"USB device {device_path} not found", 404

        log.warning("Formatting USB device %s (requested by admin)", device_path)
        self._log_audit(
            "USB_FORMAT",
            user,
            ip,
            f"device={device_path}, model={device['model']}",
        )

        ok, err = usb.format_device(device_path)
        if not ok:
            return f"Format failed: {err}", 500

        return (
            "Device formatted as ext4. Select it again to start using for recordings."
        ), 200

    def eject(self, user: str = "", ip: str = "") -> tuple[dict, str, int]:
        """Unmount USB and switch recordings back to internal storage.

        Returns (result_dict, error_string, http_status_code).
        """
        # Switch to internal storage first
        if self._storage_manager:
            self._storage_manager.set_recordings_dir(self._default_dir)

        # Unmount
        ok, err = usb.unmount_device()
        if not ok:
            log.warning("Unmount warning: %s", err)

        # Clear saved config
        self._save_usb_config("", "")

        self._log_audit(
            "USB_STORAGE_EJECTED",
            user,
            ip,
            "switched back to internal storage",
        )

        return (
            {
                "message": "USB ejected. Recording to internal storage.",
                "recordings_dir": self._default_dir,
            },
            "",
            200,
        )

    def verify_recordings_dir(self, recordings_dir: str) -> tuple[bool, str]:
        """Return whether a candidate recordings root can safely accept clips."""
        self._try_repair_recordings_dir(recordings_dir, "")
        ok, error = self._probe_recording_path(recordings_dir)
        if not ok:
            return False, error

        for camera_id in self._known_camera_ids():
            self._try_repair_recordings_dir(recordings_dir, camera_id)
            ok, error = self._probe_recording_path(recordings_dir, camera_id)
            if not ok:
                return False, f"{camera_id}: {error}"
        return True, ""

    def handle_recording_storage_fault(
        self, recordings_dir: str, camera_id: str = "", error: str = ""
    ) -> str:
        """Repair an external recording path or fail over to internal storage.

        The saved USB selection is intentionally preserved. A faulted USB should
        show up as configured-but-inactive so the operator can reselect,
        reformat, or eject it deliberately; recording continuity goes to the
        internal partition immediately.
        """
        active_dir = self._active_recordings_dir()
        if active_dir and _same_path(active_dir, recordings_dir):
            current_dir = active_dir
        elif active_dir:
            return active_dir
        else:
            current_dir = recordings_dir or self._default_dir

        if _same_path(current_dir, self._default_dir):
            log.error(
                "Internal recording storage fault at %s for %s: %s",
                current_dir,
                camera_id or "unknown camera",
                error,
            )
            return current_dir

        repair_error = ""
        if self._try_repair_recordings_dir(current_dir, camera_id):
            ok, repair_error = self._probe_recording_path(current_dir, camera_id)
            if ok:
                self._log_audit(
                    "USB_STORAGE_REPAIRED",
                    "",
                    "",
                    f"path={current_dir}, camera={camera_id or 'all'}",
                )
                return current_dir

        if self._storage_manager:
            self._storage_manager.set_recordings_dir(self._default_dir)

        detail = (
            f"path={current_dir}, fallback={self._default_dir}, "
            f"camera={camera_id or 'unknown'}, error={error or repair_error}"
        )
        log.warning("Recording storage fault; falling back to internal: %s", detail)
        self._log_audit("USB_STORAGE_FALLBACK", "", "", detail)
        return self._default_dir

    def _try_repair_recordings_dir(self, recordings_dir: str, camera_id: str) -> bool:
        if not privileged.should_use_helper():
            return False
        try:
            privileged.request(
                "recording_storage.repair_permissions",
                {"recordings_dir": recordings_dir, "camera_id": camera_id},
                timeout=60,
            )
            return True
        except privileged.PrivilegedHelperError as exc:
            log.warning("USB recording permission repair failed: %s", exc)
            return False

    def _known_camera_ids(self) -> list[str]:
        try:
            cameras = self._store.get_cameras()
        except Exception:
            return []
        ids: list[str] = []
        for camera in cameras:
            camera_id = getattr(camera, "id", "")
            if isinstance(camera_id, str) and camera_id:
                ids.append(camera_id)
        return ids

    @staticmethod
    def _probe_recording_path(
        recordings_dir: str, camera_id: str = ""
    ) -> tuple[bool, str]:
        target = Path(recordings_dir)
        if camera_id:
            target = target / camera_id
        probe = target / ".home-monitor-write-test"
        try:
            target.mkdir(parents=True, exist_ok=True)
            with open(probe, "ab") as f:
                f.write(b"ok\n")
                f.flush()
                os.fsync(f.fileno())
            probe.unlink(missing_ok=True)
            return True, ""
        except OSError as exc:
            return False, str(exc)

    def _save_usb_config(
        self,
        device_path: str,
        recordings_dir: str,
        *,
        uuid: str = "",
        label: str = "",
    ):
        """Persist USB storage selection in settings.json."""
        try:
            settings = self._store.get_settings()
            settings.usb_device = device_path
            settings.usb_uuid = uuid
            settings.usb_label = label
            settings.usb_recordings_dir = recordings_dir
            self._store.save_settings(settings)
        except Exception as e:
            log.error("Failed to save USB config: %s", e)

    def _log_audit(self, event, user, ip, detail):
        """Log audit event, swallowing errors."""
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception as e:
            log.warning("Audit log failed: %s", e)


def _storage_error_message(error: str) -> str:
    detail = (error or "unknown storage error").strip()
    return (
        "Recording storage cannot be read. "
        "Eject the USB drive or reformat/replace it before trusting recordings. "
        f"Detail: {detail}"
    )


def _same_path(left: str, right: str) -> bool:
    return os.path.normpath(left or "") == os.path.normpath(right or "")
