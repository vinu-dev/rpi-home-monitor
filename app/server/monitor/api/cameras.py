"""
Camera management API.

Endpoints:
  GET    /cameras              - list all cameras (confirmed + pending)
  POST   /cameras              - register a new camera as pending (admin)
  POST   /cameras/<id>/confirm - confirm a discovered camera (admin)
  PUT    /cameras/<id>         - update name, location, recording mode (admin)
  DELETE /cameras/<id>         - remove camera and revoke cert (admin)
  GET    /cameras/<id>/status  - live status (online, fps, uptime)
  POST   /cameras/scan         - trigger mDNS scan + return camera list (admin)
  POST   /cameras/config-notify - accept config push from camera (HMAC auth)
  POST   /cameras/heartbeat   - periodic liveness + health update from camera (HMAC auth)

Routes are thin — all orchestration is in CameraService.
"""

import hashlib
import hmac
import threading
import time

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect, login_required

# ── HMAC auth for camera M2M requests ────────────────────────────────────────
# 30-second window is tight enough to prevent meaningful replay while still
# tolerating real-world clock skew between camera and server (ADR-0016).
_HMAC_MAX_AGE = 30  # seconds (was 300 — reduced to shrink replay window)

# Thread-safe lock for per-app nonce cache mutation.
_seen_nonces_lock = threading.Lock()


def _get_seen_nonces() -> dict:
    """Return the per-app replay cache, creating it if needed.

    Stored on the app object (not module-level) so each Flask test app
    gets its own fresh cache — preventing test state bleed.
    """
    app_obj = current_app._get_current_object()
    if not hasattr(app_obj, "_hmac_seen_nonces"):
        app_obj._hmac_seen_nonces = {}
    return app_obj._hmac_seen_nonces


def _record_and_check_replay(camera_id: str, timestamp_str: str, sig: str) -> bool:
    """Return True if this (timestamp, sig) pair has been seen (replay attempt).

    Thread-safe. Automatically expires stale entries.
    """
    key = (timestamp_str, sig)
    now = time.time()

    with _seen_nonces_lock:
        nonces = _get_seen_nonces()
        camera_cache = nonces.setdefault(camera_id, {})
        # Purge expired entries (TTL = _HMAC_MAX_AGE)
        expired = [k for k, exp in camera_cache.items() if exp <= now]
        for k in expired:
            del camera_cache[k]

        if key in camera_cache:
            return True  # replay detected — reject
        camera_cache[key] = now + _HMAC_MAX_AGE
    return False


cameras_bp = Blueprint("cameras", __name__)


def _verify_camera_hmac(request) -> tuple[str, str | None]:
    """Verify HMAC-signed camera request. Shared by heartbeat + config-notify.

    Returns (camera_id, error_message). error_message is None on success.
    """
    camera_id = request.headers.get("X-Camera-ID", "")
    timestamp_str = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")

    if not camera_id or not timestamp_str or not signature:
        return "", "Missing auth headers"

    try:
        ts = int(timestamp_str)
    except ValueError:
        return "", "Invalid timestamp"

    now = int(time.time())
    if abs(now - ts) > _HMAC_MAX_AGE:
        return "", "Timestamp expired"

    camera = current_app.store.get_camera(camera_id)
    if not camera or not camera.pairing_secret:
        return "", "Unknown camera"

    body = request.get_data()
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{camera_id}:{timestamp_str}:{body_hash}"
    expected = hmac.new(
        bytes.fromhex(camera.pairing_secret),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return "", "Invalid signature"

    # Replay detection: reject the exact same signed request twice
    if _record_and_check_replay(camera_id, timestamp_str, signature):
        return "", "Duplicate request (replay detected)"

    return camera_id, None


@cameras_bp.route("", methods=["GET"])
@login_required
def list_cameras():
    """List all cameras (confirmed + pending).

    Admins see all fields including internal health metrics and camera IP.
    Viewers see only the fields needed to display and use the camera UI.
    This prevents viewers from mapping network topology or tracking occupancy.
    """
    admin_view = session.get("role") == "admin"
    cameras = current_app.camera_service.list_cameras(admin_view=admin_view)
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
    camera_id, err = _verify_camera_hmac(request)
    if err:
        status_code = 401 if err != "Invalid timestamp" else 400
        return jsonify({"error": err}), status_code

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    error, status = current_app.camera_service.accept_camera_config(camera_id, data)
    if error:
        return jsonify({"error": error}), status
    return jsonify({"message": "Config accepted"}), 200


@cameras_bp.route("/heartbeat", methods=["POST"])
def camera_heartbeat():
    """Accept periodic heartbeat from a camera.

    Updates last_seen, streaming status, and health metrics.
    Returns pending stream config if the server has unsent changes.

    Auth: HMAC-SHA256 signature using pairing_secret (ADR-0016).
    No session/CSRF — this is machine-to-machine.
    """
    camera_id, err = _verify_camera_hmac(request)
    if err:
        status_code = 401 if err != "Invalid timestamp" else 400
        return jsonify({"error": err}), status_code

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    response, error, status = current_app.camera_service.accept_heartbeat(
        camera_id, data
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(response), 200


@cameras_bp.route("/scan", methods=["POST"])
@admin_required
@csrf_protect
def scan_cameras():
    """Trigger an mDNS scan and return current camera list.

    Sends an immediate PTR query for _rtsp._tcp on the local network.
    The background ServiceBrowser processes responses and calls report_camera()
    for any new cameras, which adds them as pending entries.

    Returns the current camera list (same as GET /cameras) so the dashboard
    can update in a single round-trip.
    """
    current_app.discovery_service.trigger_scan()
    cameras = current_app.camera_service.list_cameras(admin_view=True)
    return jsonify(cameras), 200


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
