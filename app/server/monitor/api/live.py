"""
Live streaming API.

Endpoints:
  GET /live/<cam-id>/stream.m3u8  - HLS playlist for live view
  GET /live/<cam-id>/snapshot     - current frame as JPEG

Note: HLS segment files (.ts) are served directly by nginx,
not through Flask. This blueprint handles playlist generation
and snapshot extraction.
"""

from pathlib import Path

from flask import Blueprint, current_app, jsonify, send_file

from monitor.auth import login_required

live_bp = Blueprint("live", __name__)


@live_bp.route("/<camera_id>/stream.m3u8", methods=["GET"])
@login_required
def hls_playlist(camera_id):
    """Serve the HLS playlist for a camera's live stream."""
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    if camera.status != "online":
        return jsonify({"error": "Camera is not online"}), 503

    live_dir = Path(current_app.config["LIVE_DIR"])
    playlist = live_dir / camera_id / "stream.m3u8"

    if not playlist.is_file():
        return jsonify({"error": "Stream not available"}), 503

    return send_file(str(playlist), mimetype="application/vnd.apple.mpegurl")


@live_bp.route("/<camera_id>/snapshot", methods=["GET"])
@login_required
def snapshot(camera_id):
    """Serve the latest snapshot JPEG for a camera."""
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    if camera.status != "online":
        return jsonify({"error": "Camera is not online"}), 503

    live_dir = Path(current_app.config["LIVE_DIR"])
    snap = live_dir / camera_id / "snapshot.jpg"

    if not snap.is_file():
        return jsonify({"error": "Snapshot not available"}), 503

    return send_file(str(snap), mimetype="image/jpeg")
