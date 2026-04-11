"""
Recordings API.

Endpoints:
  GET    /recordings/<cam-id>?date=YYYY-MM-DD  - list clips for a camera/date
  GET    /recordings/<cam-id>/dates             - list dates with clips
  GET    /recordings/<cam-id>/latest            - most recent clip
  DELETE /recordings/<cam-id>/<date>/<filename> - delete a clip (admin)
"""

import os
from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request, send_file, session

from monitor.auth import admin_required, login_required
from monitor.services.recorder import RecorderService

recordings_bp = Blueprint("recordings", __name__)


def _get_recorder() -> RecorderService:
    """Get recorder service using the current recordings directory.

    Uses StorageManager's live path (which may be USB) rather than
    the static RECORDINGS_DIR from config.
    """
    storage = getattr(current_app, "storage_manager", None)
    recordings_dir = (
        storage.recordings_dir if storage else current_app.config["RECORDINGS_DIR"]
    )
    return RecorderService(
        recordings_dir,
        current_app.config["LIVE_DIR"],
    )


@recordings_bp.route("/<camera_id>", methods=["GET"])
@login_required
def list_clips(camera_id):
    """List clips for a camera, optionally filtered by date."""
    # Verify camera exists
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    clip_date = request.args.get("date", "")
    recorder = _get_recorder()
    clips = recorder.list_clips(camera_id, clip_date)

    return jsonify([asdict(c) for c in clips]), 200


@recordings_bp.route("/<camera_id>/dates", methods=["GET"])
@login_required
def list_dates(camera_id):
    """List dates that have recordings for a camera."""
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    recorder = _get_recorder()
    dates = recorder.get_dates_with_clips(camera_id)
    return jsonify({"camera_id": camera_id, "dates": dates}), 200


@recordings_bp.route("/<camera_id>/latest", methods=["GET"])
@login_required
def latest_clip(camera_id):
    """Get the most recent clip for a camera."""
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    recorder = _get_recorder()
    clip = recorder.get_latest_clip(camera_id)
    if clip is None:
        return jsonify({"error": "No recordings found"}), 404

    return jsonify(asdict(clip)), 200


@recordings_bp.route("/<camera_id>/<clip_date>/<filename>", methods=["GET"])
@login_required
def get_clip(camera_id, clip_date, filename):
    """Serve a clip file. Works for both internal and USB storage."""
    if not filename.endswith(".mp4"):
        return jsonify({"error": "Invalid filename"}), 400

    recorder = _get_recorder()
    clip_path = os.path.join(recorder._recordings_dir, camera_id, clip_date, filename)
    if not os.path.isfile(clip_path):
        return jsonify({"error": "Clip not found"}), 404

    return send_file(clip_path, mimetype="video/mp4")


@recordings_bp.route("/<camera_id>/<clip_date>/<filename>", methods=["DELETE"])
@admin_required
def delete_clip(camera_id, clip_date, filename):
    """Delete a specific clip. Admin only."""
    # Basic input validation
    if not filename.endswith(".mp4"):
        return jsonify({"error": "Invalid filename"}), 400

    recorder = _get_recorder()
    deleted = recorder.delete_clip(camera_id, clip_date, filename)
    if not deleted:
        return jsonify({"error": "Clip not found"}), 404

    audit = getattr(current_app, "audit", None)
    if audit:
        audit.log_event(
            "CLIP_DELETED",
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"deleted {camera_id}/{clip_date}/{filename}",
        )

    return jsonify({"message": "Clip deleted"}), 200
