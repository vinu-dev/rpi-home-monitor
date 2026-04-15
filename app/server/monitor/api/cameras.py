"""
Camera management API.

Endpoints:
  GET    /cameras              - list all cameras (confirmed + pending)
  POST   /cameras              - register a new camera as pending (admin)
  POST   /cameras/<id>/confirm - confirm a discovered camera (admin)
  PUT    /cameras/<id>         - update name, location, recording mode (admin)
  DELETE /cameras/<id>         - remove camera and revoke cert (admin)
  GET    /cameras/<id>/status  - live status (online, fps, uptime)
  POST   /cameras/config-notify - accept config push from camera (HMAC auth)

Routes are thin — all orchestration is in CameraService.
"""

import hashlib
import hmac
import time

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect, login_required

# Max age for HMAC timestamp (seconds)
_HMAC_MAX_AGE = 300

cameras_bp = Blueprint("cameras", __name__)


@cameras_bp.route("", methods=["GET"])
@login_required
def list_cameras():
    """List all cameras (confirmed + pending)."""
    cameras = current_app.camera_service.list_cameras()
    return jsonify(cameras), 200


@cameras_bp.route("", methods=["POST"])
@admin_required
@csrf_protect
def add_camera():
    """Register a new camera as pending. Admin only."""
    data = request.get_json(silent=True) or {}
    result, error, status = current_app.camera_service.add_camera(
        camera_id=data.get("id", ""),
        name=data.get("name", ""),
        location=data.get("location", ""),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@cameras_bp.route("/config-notify", methods=["POST"])
def config_notify():
    """Accept config notification from a camera.

    Auth: HMAC-SHA256 signature using pairing_secret.
    No session/CSRF — this is machine-to-machine (ADR-0015).
    """
    camera_id = request.headers.get("X-Camera-ID", "")
    timestamp_str = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")

    if not camera_id or not timestamp_str or not signature:
        return jsonify({"error": "Missing auth headers"}), 401

    # Validate timestamp freshness
    try:
        ts = int(timestamp_str)
    except ValueError:
        return jsonify({"error": "Invalid timestamp"}), 400

    now = int(time.time())
    if abs(now - ts) > _HMAC_MAX_AGE:
        return jsonify({"error": "Timestamp expired"}), 401

    # Look up camera and its pairing secret
    camera = current_app.store.get_camera(camera_id)
    if not camera or not camera.pairing_secret:
        return jsonify({"error": "Unknown camera"}), 401

    # Verify HMAC
    body = request.get_data()
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{camera_id}:{timestamp_str}:{body_hash}"
    expected = hmac.new(
        bytes.fromhex(camera.pairing_secret),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return jsonify({"error": "Invalid signature"}), 401

    # Parse and accept config
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    error, status = current_app.camera_service.accept_camera_config(camera_id, data)
    if error:
        return jsonify({"error": error}), status
    return jsonify({"message": "Config accepted"}), 200


@cameras_bp.route("/<camera_id>/confirm", methods=["POST"])
@admin_required
@csrf_protect
def confirm_camera(camera_id):
    """Confirm a discovered (pending) camera. Admin only."""
    data = request.get_json(silent=True) or {}
    result, error, status = current_app.camera_service.confirm(
        camera_id,
        name=data.get("name", ""),
        location=data.get("location", ""),
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@cameras_bp.route("/<camera_id>", methods=["PUT"])
@admin_required
@csrf_protect
def update_camera(camera_id):
    """Update camera settings. Admin only."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    error, status = current_app.camera_service.update(
        camera_id,
        data,
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"message": "Camera updated"}), 200


@cameras_bp.route("/<camera_id>", methods=["DELETE"])
@admin_required
@csrf_protect
def delete_camera(camera_id):
    """Remove a camera and revoke its cert. Admin only."""
    # Revoke cert first (if paired)
    if hasattr(current_app, "pairing_service"):
        current_app.pairing_service.unpair(
            camera_id,
            user=session.get("username", ""),
            ip=request.remote_addr or "",
        )

    error, status = current_app.camera_service.delete(
        camera_id,
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"message": "Camera removed"}), 200


@cameras_bp.route("/<camera_id>/status", methods=["GET"])
@login_required
def camera_status(camera_id):
    """Get live status for a camera."""
    result, error = current_app.camera_service.get_camera_status(camera_id)
    if error:
        return jsonify({"error": error}), 404
    return jsonify(result), 200
