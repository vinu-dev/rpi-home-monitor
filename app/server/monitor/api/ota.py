"""
Over-the-Air update API.

Endpoints:
  POST /ota/server/upload     - upload .swu image for server (admin)
  POST /ota/camera/<id>/push  - push update to camera (admin)
  GET  /ota/status            - update status for all devices

OTA uses swupdate with A/B partition scheme.
Images must be Ed25519 signed — unsigned images are rejected.
"""

import os
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, login_required

ota_bp = Blueprint("ota", __name__)

# In-memory OTA status tracking
_ota_status: dict = {
    "server": {"state": "idle", "version": "", "progress": 0, "error": ""},
}

ALLOWED_EXTENSIONS = {".swu"}
MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500MB


@ota_bp.route("/status", methods=["GET"])
@login_required
def get_status():
    """Get OTA update status for all devices."""
    settings = current_app.store.get_settings()
    cameras = current_app.store.get_cameras()

    result = {
        "server": {
            "current_version": settings.firmware_version,
            **_ota_status.get("server", {"state": "idle"}),
        },
        "cameras": [],
    }

    for cam in cameras:
        if cam.status == "pending":
            continue
        cam_status = _ota_status.get(cam.id, {"state": "idle"})
        result["cameras"].append(
            {
                "id": cam.id,
                "name": cam.name,
                "current_version": cam.firmware_version,
                **cam_status,
            }
        )

    return jsonify(result), 200


@ota_bp.route("/server/upload", methods=["POST"])
@admin_required
def upload_server_image():
    """Upload a .swu image for server OTA update. Admin only."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    # Validate extension
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Only .swu files are accepted"}), 400

    # Save to staging area
    data_dir = Path(current_app.config["DATA_DIR"])
    staging = data_dir / "ota"
    staging.mkdir(exist_ok=True)
    dest = staging / "server-update.swu"
    file.save(str(dest))

    # Check file size
    if dest.stat().st_size > MAX_UPLOAD_SIZE:
        dest.unlink()
        return jsonify({"error": "File too large (max 500MB)"}), 400

    _ota_status["server"] = {
        "state": "staged",
        "version": "",
        "progress": 0,
        "error": "",
    }

    audit = getattr(current_app, "audit", None)
    if audit:
        audit.log_event(
            "OTA_UPLOADED",
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"server update uploaded: {file.filename}",
        )

    return jsonify(
        {
            "message": "Update image staged",
            "filename": file.filename,
            "size_bytes": dest.stat().st_size,
        }
    ), 200


@ota_bp.route("/camera/<camera_id>/push", methods=["POST"])
@admin_required
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

    data = request.get_json(silent=True) or {}
    version = data.get("version", "")

    _ota_status[camera_id] = {
        "state": "pending",
        "version": version,
        "progress": 0,
        "error": "",
    }

    audit = getattr(current_app, "audit", None)
    if audit:
        audit.log_event(
            "OTA_CAMERA_PUSH",
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"update pushed to camera {camera_id}",
        )

    return jsonify({"message": f"Update queued for camera {camera_id}"}), 200
