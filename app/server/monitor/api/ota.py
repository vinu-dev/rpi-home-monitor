"""
Over-the-Air update API.

Endpoints:
  POST /ota/server/upload      - upload .swu image for server (admin)
  POST /ota/server/install     - install staged bundle (admin)
  POST /ota/camera/<id>/push   - push update to camera (admin)
  GET  /ota/status             - update status for all devices
  GET  /ota/usb/scan           - scan USB devices for .swu bundles (admin)
  POST /ota/usb/import         - import .swu bundle from USB (admin)

OTA uses swupdate with A/B partition scheme.
Production images are verified via CMS signatures and SWUpdate certificates.
"""

import os
import tempfile

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect, login_required

ota_bp = Blueprint("ota", __name__)


@ota_bp.route("/status", methods=["GET"])
@login_required
def get_status():
    """Get OTA update status for all devices."""
    settings = current_app.store.get_settings()
    cameras = current_app.store.get_cameras()
    ota = current_app.ota_service

    result = {
        "server": {
            "current_version": settings.firmware_version,
            **ota.get_status("server"),
        },
        "cameras": [],
    }

    for cam in cameras:
        if cam.status == "pending":
            continue
        result["cameras"].append(
            {
                "id": cam.id,
                "name": cam.name,
                "current_version": cam.firmware_version,
                **ota.get_status(cam.id),
            }
        )

    return jsonify(result), 200


@ota_bp.route("/server/upload", methods=["POST"])
@admin_required
@csrf_protect
def upload_server_image():
    """Upload a .swu image for server OTA update. Admin only."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    ota = current_app.ota_service
    user = session.get("username", "")
    ip = request.remote_addr or ""

    # Save upload to temp file first
    try:
        os.makedirs(ota.inbox_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(suffix=".swu", dir=ota.inbox_dir)
        with os.fdopen(fd, "wb") as f:
            file.save(f)
    except OSError as e:
        return jsonify({"error": f"Upload failed: {e}"}), 500

    # Stage the bundle (validates extension, size, disk space)
    staged_path, err = ota.stage_bundle(tmp_path, file.filename, user=user, ip=ip)
    if err:
        # Clean up temp file if staging failed
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": err}), 400

    # Verify bundle signature
    valid, verify_err = ota.verify_bundle(staged_path)
    if not valid:
        ota.clean_staging()
        return jsonify({"error": f"Verification failed: {verify_err}"}), 400

    return jsonify(
        {
            "message": "Update image staged and verified",
            "filename": file.filename,
            "staged_path": staged_path,
        }
    ), 200


@ota_bp.route("/server/install", methods=["POST"])
@admin_required
@csrf_protect
def install_server_image():
    """Install a staged .swu bundle. Admin only."""
    ota = current_app.ota_service
    user = session.get("username", "")
    ip = request.remote_addr or ""

    # Find staged bundle
    staging = ota.staging_dir
    if not os.path.isdir(staging):
        return jsonify({"error": "No staged update found"}), 404

    bundles = [f for f in os.listdir(staging) if f.endswith(".swu")]
    if not bundles:
        return jsonify({"error": "No staged update found"}), 404

    bundle_path = os.path.join(staging, bundles[0])
    ok, err = ota.install_bundle(bundle_path, user=user, ip=ip)
    if not ok:
        return jsonify({"error": err}), 500

    return jsonify({"message": "Installation complete — reboot required"}), 200


@ota_bp.route("/camera/<camera_id>/push", methods=["POST"])
@admin_required
@csrf_protect
def push_camera_update(camera_id):
    """Push an update to a camera. Admin only.

    In production, this triggers the actual swupdate process on the camera.
    For now, it validates the request and stages the update.
    """
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    if camera.status != "online":
        return jsonify({"error": "Camera must be online to receive updates"}), 400

    ota = current_app.ota_service
    data = request.get_json(silent=True) or {}
    version = data.get("version", "")

    ota.set_status(camera_id, "pending", version=version)

    audit = getattr(current_app, "audit", None)
    if audit:
        audit.log_event(
            "OTA_CAMERA_PUSH",
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"update pushed to camera {camera_id}",
        )

    return jsonify({"message": f"Update queued for camera {camera_id}"}), 200


@ota_bp.route("/usb/scan", methods=["GET"])
@admin_required
def scan_usb():
    """Scan USB devices for .swu update bundles. Admin only."""
    ota = current_app.ota_service
    bundles = ota.scan_usb()
    return jsonify({"bundles": bundles}), 200


@ota_bp.route("/usb/import", methods=["POST"])
@admin_required
@csrf_protect
def import_from_usb():
    """Import a .swu bundle from a USB device. Admin only.

    Request body: {"path": "/mnt/recordings/updates/update-1.2.swu"}
    """
    ota = current_app.ota_service
    data = request.get_json(silent=True) or {}
    usb_path = data.get("path", "")

    if not usb_path:
        return jsonify({"error": "No file path provided"}), 400

    user = session.get("username", "")
    ip = request.remote_addr or ""

    staged_path, err = ota.import_from_usb(usb_path, user=user, ip=ip)
    if err:
        return jsonify({"error": err}), 400

    # Verify bundle signature
    valid, verify_err = ota.verify_bundle(staged_path)
    if not valid:
        ota.clean_staging()
        return jsonify({"error": f"Verification failed: {verify_err}"}), 400

    return jsonify(
        {
            "message": "USB bundle imported, staged, and verified",
            "staged_path": staged_path,
        }
    ), 200
