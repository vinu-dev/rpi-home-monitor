"""
Camera management API.

Endpoints:
  GET    /cameras              - list all cameras (confirmed + pending)
  POST   /cameras              - register a new camera as pending (admin)
  POST   /cameras/<id>/confirm - confirm a discovered camera (admin)
  PUT    /cameras/<id>         - update name, location, recording mode (admin)
  DELETE /cameras/<id>         - remove camera and revoke cert (admin)
  GET    /cameras/<id>/status  - live status (online, fps, uptime)

Routes are thin — all orchestration is in CameraService.
"""

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, login_required

cameras_bp = Blueprint("cameras", __name__)


@cameras_bp.route("", methods=["GET"])
@login_required
def list_cameras():
    """List all cameras (confirmed + pending)."""
    cameras = current_app.camera_service.list_cameras()
    return jsonify(cameras), 200


@cameras_bp.route("", methods=["POST"])
@admin_required
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


@cameras_bp.route("/<camera_id>/confirm", methods=["POST"])
@admin_required
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
