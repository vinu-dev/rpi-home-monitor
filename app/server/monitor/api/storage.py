"""
Storage API — USB device management and recording storage.

Endpoints:
  GET  /storage/status   - current storage info + recording location
  GET  /storage/devices  - list USB block devices
  POST /storage/select   - select USB device for recordings
  POST /storage/format   - format unsupported USB device to ext4
  POST /storage/eject    - unmount USB, switch back to /data
"""
import logging
import os

from flask import Blueprint, current_app, jsonify, request

from monitor.auth import admin_required
from monitor.services import usb

log = logging.getLogger("monitor.api.storage")

storage_bp = Blueprint("storage", __name__)


@storage_bp.route("/status", methods=["GET"])
@admin_required
def get_status():
    """Return current storage info and recording location."""
    storage = getattr(current_app, "storage_manager", None)
    if not storage:
        return jsonify({"error": "Storage manager not initialized"}), 500

    stats = storage.get_storage_stats()
    return jsonify(stats), 200


@storage_bp.route("/devices", methods=["GET"])
@admin_required
def list_devices():
    """List USB block devices available for recording storage."""
    devices = usb.detect_devices()
    return jsonify({"devices": devices}), 200


@storage_bp.route("/select", methods=["POST"])
@admin_required
def select_device():
    """Select a USB device for recordings.

    If filesystem is supported, mounts and creates recordings folder.
    If unsupported, returns error suggesting format.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    device_path = (data.get("device_path") or "").strip()
    if not device_path:
        return jsonify({"error": "device_path required"}), 400

    # Find the device
    devices = usb.detect_devices()
    device = None
    for d in devices:
        if d["path"] == device_path:
            device = d
            break

    if not device:
        return jsonify({"error": f"Device {device_path} not found"}), 404

    # Check filesystem
    if not device["supported"]:
        return jsonify({
            "error": f"Filesystem '{device['fstype']}' not supported. "
                     f"Format the device first via POST /storage/format.",
            "needs_format": True,
            "fstype": device["fstype"],
        }), 400

    # Mount the device
    ok, err = usb.mount_device(device_path)
    if not ok:
        return jsonify({"error": f"Failed to mount: {err}"}), 500

    # Create recordings folder
    rec_dir = usb.prepare_recordings_dir()

    # Update storage manager to use USB
    storage = getattr(current_app, "storage_manager", None)
    if storage:
        storage.set_recordings_dir(rec_dir)

    # Persist USB config in settings
    _save_usb_config(device_path, rec_dir)

    _audit("USB_STORAGE_SELECTED",
           f"device={device_path}, mount={usb.DEFAULT_MOUNT_POINT}")

    return jsonify({
        "message": f"USB storage active: {device['model']} ({device['size']})",
        "recordings_dir": rec_dir,
        "device": device,
    }), 200


@storage_bp.route("/format", methods=["POST"])
@admin_required
def format_device():
    """Format a USB device to ext4.

    Only needed if the device has an unsupported filesystem.
    WARNING: This erases all data on the device.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    device_path = (data.get("device_path") or "").strip()
    confirm = data.get("confirm", False)

    if not device_path:
        return jsonify({"error": "device_path required"}), 400
    if not confirm:
        return jsonify({
            "error": "Format requires confirm=true. "
                     "WARNING: This will ERASE ALL DATA on the device.",
            "needs_confirmation": True,
        }), 400

    # Safety: verify it's actually a USB device
    devices = usb.detect_devices()
    device = None
    for d in devices:
        if d["path"] == device_path:
            device = d
            break

    if not device:
        return jsonify({"error": f"USB device {device_path} not found"}), 404

    log.warning("Formatting USB device %s (requested by admin)", device_path)
    _audit("USB_FORMAT", f"device={device_path}, model={device['model']}")

    ok, err = usb.format_device(device_path)
    if not ok:
        return jsonify({"error": f"Format failed: {err}"}), 500

    return jsonify({
        "message": f"Device formatted as ext4. "
                   f"Select it again to start using for recordings.",
    }), 200


@storage_bp.route("/eject", methods=["POST"])
@admin_required
def eject_device():
    """Unmount USB and switch recordings back to /data."""
    storage = getattr(current_app, "storage_manager", None)

    # Switch recordings back to internal storage first
    default_dir = current_app.config.get("RECORDINGS_DIR", "/data/recordings")
    if storage:
        storage.set_recordings_dir(default_dir)

    # Unmount USB
    ok, err = usb.unmount_device()
    if not ok:
        log.warning("Unmount warning: %s", err)

    # Clear USB config
    _save_usb_config("", "")

    _audit("USB_STORAGE_EJECTED", "switched back to internal storage")

    return jsonify({
        "message": "USB ejected. Recording to internal storage.",
        "recordings_dir": default_dir,
    }), 200


def _save_usb_config(device_path: str, recordings_dir: str):
    """Persist USB storage selection in settings.json."""
    try:
        settings = current_app.store.get_settings()
        settings.usb_device = device_path
        settings.usb_recordings_dir = recordings_dir
        current_app.store.save_settings(settings)
    except Exception as e:
        log.error("Failed to save USB config: %s", e)


def _audit(event: str, detail: str):
    """Log an audit event."""
    audit = getattr(current_app, "audit", None)
    if audit:
        from flask import session
        audit.log_event(
            event,
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=detail,
        )
