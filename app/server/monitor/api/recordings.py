"""
Recordings API — thin HTTP adapter.

Delegates all business logic to RecordingsService.

Endpoints:
  GET    /recordings/cameras                    - cameras eligible to browse
                                                   (paired + orphaned archives)
  GET    /recordings/<cam-id>?date=YYYY-MM-DD  - list clips for a camera/date
  GET    /recordings/<cam-id>/dates             - list dates with clips
  GET    /recordings/<cam-id>/latest            - most recent clip
  GET    /recordings/<cam-id>/<date>/<filename> - serve a clip file
  DELETE /recordings/<cam-id>/<date>/<filename> - delete a clip (admin)
  DELETE /recordings/<cam-id>/<date>            - delete all clips on a date (admin)
  DELETE /recordings/<cam-id>                   - delete all clips for a camera (admin)

Route ordering: Werkzeug prefers static segments over variable segments,
so ``/recordings/cameras`` wins over ``/recordings/<camera_id>`` — safe.
"""

from flask import Blueprint, current_app, jsonify, request, send_file, session

from monitor.auth import admin_required, csrf_protect, login_required

recordings_bp = Blueprint("recordings", __name__)


def _svc():
    """Get the recordings service from the app."""
    return current_app.recordings_service


@recordings_bp.route("/latest", methods=["GET"])
@login_required
def latest_across_cameras():
    """Return the newest clip across every camera (ADR-0018 Tier-2).

    Distinct from ``/recordings/<camera_id>/latest`` — this variant has no
    camera path segment and scans all paired cameras + orphan archives.
    """
    result, error, status = _svc().latest_across_cameras()
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@recordings_bp.route("/recent", methods=["GET"])
@login_required
def recent_across_cameras():
    """Return the most recent N clips across every camera (ADR-0018 Tier-3).

    Query params:
      ``limit`` — rows to return (1..50, default 10).
    """
    try:
        limit = int(request.args.get("limit", 10))
    except ValueError:
        limit = 10
    result, error, status = _svc().recent_across_cameras(limit=limit)
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@recordings_bp.route("/cameras", methods=["GET"])
@login_required
def list_camera_sources():
    """List cameras that can appear in the Recordings tab.

    Returns paired cameras (online/offline) and orphaned archives
    (``status=removed``) whose Camera record was deleted but whose
    clips remain on disk.
    """
    result, error, status = _svc().list_camera_sources()
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@recordings_bp.route("/<camera_id>", methods=["GET"])
@login_required
def list_clips(camera_id):
    """List clips for a camera, optionally filtered by date."""
    clip_date = request.args.get("date", "")
    result, error, status = _svc().list_clips(camera_id, clip_date)
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@recordings_bp.route("/<camera_id>/dates", methods=["GET"])
@login_required
def list_dates(camera_id):
    """List dates that have recordings for a camera."""
    result, error, status = _svc().list_dates(camera_id)
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@recordings_bp.route("/<camera_id>/latest", methods=["GET"])
@login_required
def latest_clip(camera_id):
    """Get the most recent clip for a camera."""
    result, error, status = _svc().latest_clip(camera_id)
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@recordings_bp.route("/<camera_id>/<clip_date>/<filename>", methods=["GET"])
@login_required
def get_clip(camera_id, clip_date, filename):
    """Serve a clip file."""
    clip_path, error, status = _svc().resolve_clip_path(camera_id, clip_date, filename)
    if error:
        return jsonify({"error": error}), status
    return send_file(clip_path, mimetype="video/mp4")


@recordings_bp.route("/<camera_id>/<clip_date>/<filename>", methods=["DELETE"])
@admin_required
@csrf_protect
def delete_clip(camera_id, clip_date, filename):
    """Delete a specific clip. Admin only."""
    result, error, status = _svc().delete_clip(
        camera_id,
        clip_date,
        filename,
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@recordings_bp.route("/<camera_id>/<clip_date>", methods=["DELETE"])
@admin_required
@csrf_protect
def delete_date(camera_id, clip_date):
    """Delete all clips for a camera on a given date. Admin only."""
    result, error, status = _svc().delete_date(
        camera_id,
        clip_date,
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@recordings_bp.route("/<camera_id>", methods=["DELETE"])
@admin_required
@csrf_protect
def delete_camera_recordings(camera_id):
    """Delete all recordings for a camera across every date. Admin only.

    The Camera record itself is not affected — remove/unpair is a
    separate action under /api/v1/cameras.
    """
    result, error, status = _svc().delete_camera_recordings(
        camera_id,
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@recordings_bp.route("", methods=["DELETE"])
@admin_required
@csrf_protect
def delete_all_recordings():
    """Nuke every clip across every camera. Danger-zone op (issue #106).

    Gated by admin_required + csrf_protect like every other destructive
    operation. Emits a single ``RECORDINGS_DELETED_ALL`` audit event
    with the clip count and bytes freed for post-hoc review.
    """
    result, error, status = _svc().delete_all_recordings(
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status
