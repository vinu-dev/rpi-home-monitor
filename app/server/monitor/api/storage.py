"""
Storage API — USB device management and recording storage.

Endpoints:
  GET  /storage/status   - current storage info + recording location
  GET  /storage/devices  - list USB block devices
  POST /storage/select   - select USB device for recordings
  POST /storage/format   - format unsupported USB device to ext4
  POST /storage/eject    - unmount USB, switch back to /data

Routes are thin — all orchestration is in StorageService.
"""

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required

storage_bp = Blueprint("storage", __name__)


@storage_bp.route("/status", methods=["GET"])
@admin_required
def get_status():
    """Return current storage info and recording location."""
    stats, error = current_app.storage_service.get_status()
    if error:
        return jsonify({"error": error}), 500
    return jsonify(stats), 200


@storage_bp.route("/devices", methods=["GET"])
@admin_required
def list_devices():
    """List USB block devices available for recording storage."""
    devices = current_app.storage_service.list_devices()
    return jsonify({"devices": devices}), 200


@storage_bp.route("/select", methods=["POST"])
@admin_required
def select_device():
    """Select a USB device for recordings."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    result, error, status = current_app.storage_service.select_device(
        device_path=(data.get("device_path") or "").strip(),
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        # Include extra fields (needs_format, fstype) if present
        resp = {"error": error}
        if result:
            resp.update(result)
        return jsonify(resp), status
    return jsonify(result), status


@storage_bp.route("/format", methods=["POST"])
@admin_required
def format_device():
    """Format a USB device to ext4."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    device_path = (data.get("device_path") or "").strip()
    confirm = data.get("confirm", False)

    msg, status = current_app.storage_service.format_device(
        device_path,
        confirm,
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if status == 200:
        return jsonify({"message": msg}), 200
    # Error responses: include needs_confirmation flag for missing confirm
    resp = {"error": msg}
    if not confirm and device_path:
        resp["needs_confirmation"] = True
    return jsonify(resp), status


@storage_bp.route("/eject", methods=["POST"])
@admin_required
def eject_device():
    """Unmount USB and switch recordings back to /data."""
    result, error, status = current_app.storage_service.eject(
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status
