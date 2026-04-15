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


@live_bp.route("/<camera_id>/<path:filename>", methods=["GET"])
@login_required
def hls_segment(camera_id, filename):
    """Serve an HLS segment (.ts) or any live file for a camera.

    Previously served directly by nginx without auth. Now routed
    through Flask to enforce session validation via @login_required.
    """
    # Only serve expected file types
    if not filename.endswith((".ts", ".m3u8", ".jpg")):
        return jsonify({"error": "Invalid file type"}), 400

    live_dir = Path(current_app.config["LIVE_DIR"])
    file_path = live_dir / camera_id / filename

    # Prevent path traversal
    try:
        file_path.resolve().relative_to(live_dir.resolve())
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400

    if not file_path.is_file():
        return jsonify({"error": "File not found"}), 404

    mimetypes = {
        ".ts": "video/mp2t",
        ".m3u8": "application/vnd.apple.mpegurl",
        ".jpg": "image/jpeg",
    }
    mimetype = mimetypes.get(file_path.suffix, "application/octet-stream")
    return send_file(str(file_path), mimetype=mimetype)


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
