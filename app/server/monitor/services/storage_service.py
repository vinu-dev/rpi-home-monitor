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

from monitor.services import usb

log = logging.getLogger("monitor.storage_service")


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
        stats = self._storage_manager.get_storage_stats()
        return stats, ""

    def list_devices(self) -> list[dict]:
        """List available USB block devices."""
        return usb.detect_devices()

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
            return (
                {
                    "needs_format": True,
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
        rec_dir = usb.prepare_recordings_dir()

        # Switch storage manager
        if self._storage_manager:
            self._storage_manager.set_recordings_dir(rec_dir)

        # Persist config
        self._save_usb_config(device_path, rec_dir)

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

    def _save_usb_config(self, device_path: str, recordings_dir: str):
        """Persist USB storage selection in settings.json."""
        try:
            settings = self._store.get_settings()
            settings.usb_device = device_path
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
