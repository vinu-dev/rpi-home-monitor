# REQ: SWR-003; RISK: RISK-002; SEC: SC-002; TEST: TC-008, TC-012
"""
Camera pairing API.

Endpoints:
  POST /cameras/<id>/pair   - initiate pairing, generate PIN (admin)
  POST /cameras/<id>/unpair - revoke cert, reset pairing (admin)
  POST /pair/exchange        - camera trades PIN for certs (no auth — PIN is auth)
  POST /pair/register        - camera self-registers as pending (rate-limited)

The exchange endpoint intentionally has no session auth — the 6-digit PIN
(rate-limited, 5-min expiry) is the authentication mechanism for the camera.

Security notes:
- /pair/register is rate-limited (10 per IP per 5 min) to prevent fake camera spam.
- /pair/exchange already has 3-attempt lockout per camera in PairingService.
- Error messages from pairing_service are sanitised before returning to clients
  to avoid leaking internal paths (e.g. CA cert location).
"""

import time

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect

pairing_bp = Blueprint("pairing", __name__)

# ── /pair/register rate limiter ───────────────────────────────────────────────
# Prevents a LAN attacker from flooding the dashboard with fake pending cameras.
_register_attempts: dict[str, list[float]] = {}
_REGISTER_RATE_WINDOW = 300  # seconds (5 minutes)
_REGISTER_RATE_MAX = 10  # max registrations per window per IP


def _register_rate_limited(ip: str) -> bool:
    """Return True if the IP has exceeded the registration rate limit."""
    now = time.time()
    attempts = [
        t for t in _register_attempts.get(ip, []) if now - t < _REGISTER_RATE_WINDOW
    ]
    _register_attempts[ip] = attempts
    if len(attempts) >= _REGISTER_RATE_MAX:
        return True
    attempts.append(now)
    _register_attempts[ip] = attempts
    return False


def _safe_pairing_error(error: str) -> str:
    """Sanitise internal pairing error messages for client responses.

    Prevents leaking internal paths, CA cert locations, or OpenSSL details
    that could aid an attacker doing reconnaissance.
    """
    internal_phrases = (
        "CA key or certificate not found",
        "CA certificate not found",
        "OpenSSL error",
        "Failed to read generated certificate",
        "File operation failed",
    )
    for phrase in internal_phrases:
        if phrase in error:
            return "Pairing setup error, please contact the administrator"
    return error


@pairing_bp.route("/cameras/<camera_id>/pair", methods=["POST"])
@admin_required
@csrf_protect
def initiate_pairing(camera_id):
    """Initiate pairing for a camera. Admin only.

    Returns a 6-digit PIN to display on the dashboard.
    """
    pin, error, status = current_app.pairing_service.initiate_pairing(
        camera_id,
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": _safe_pairing_error(error)}), status
    return jsonify({"pin": pin, "expires_in": 300}), 200


@pairing_bp.route("/cameras/<camera_id>/unpair", methods=["POST"])
@admin_required
@csrf_protect
def unpair_camera(camera_id):
    """Unpair a camera and revoke its certificate. Admin only."""
    error, status = current_app.pairing_service.unpair(
        camera_id,
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status

    # Stop streaming pipeline when camera is unpaired
    try:
        current_app.streaming.stop_camera(camera_id)
    except Exception:
        pass

    return jsonify({"message": "Camera unpaired"}), 200


@pairing_bp.route("/pair/register", methods=["POST"])
def register_camera():
    """Camera self-registers as pending on the server.

    No session auth — camera calls this before pairing to appear in
    the dashboard. The server creates a pending entry if it doesn't
    already exist.

    Rate-limited: 10 requests per IP per 5 minutes to prevent fake camera spam.
    Camera ID format is validated to the cam-<hex> pattern.
    """
    ip = request.remote_addr or ""
    if _register_rate_limited(ip):
        return jsonify({"error": "Too many registration requests"}), 429

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    camera_id = data.get("camera_id", "")
    if not camera_id:
        return jsonify({"error": "camera_id required"}), 400

    # Validate camera ID format before persisting it
    import re

    if not re.match(r"^cam-[a-z0-9]{1,48}$", camera_id):
        return jsonify({"error": "Invalid camera_id format"}), 400

    # A camera that calls /pair/register is by definition asking to pair,
    # so treat it as explicitly unpaired. This guarantees that if the server
    # already has a stale "online" row for this camera_id it gets reset to
    # "pending" and the admin sees it in the Discovered section.
    current_app.discovery_service.report_camera(
        camera_id=camera_id,
        ip=ip,
        firmware_version=data.get("firmware_version", ""),
        paired=False,
    )
    return jsonify({"status": "registered"}), 200


@pairing_bp.route("/pair/exchange", methods=["POST"])
def exchange_certs():
    """Camera exchanges PIN for certificates and pairing secret.

    No session auth required — the PIN is the authentication.
    Rate-limited to 3 attempts per camera per 5-minute window.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    pin = data.get("pin", "")
    camera_id = data.get("camera_id", "")

    if not pin or not camera_id:
        return jsonify({"error": "pin and camera_id are required"}), 400

    result, error, status = current_app.pairing_service.exchange_certs(
        pin,
        camera_id,
        ip=request.remote_addr or "",
        status_cert=data.get("status_cert", ""),
    )
    if error:
        return jsonify({"error": _safe_pairing_error(error)}), status

    # Start streaming immediately if camera is set to continuous recording.
    # Without this, streaming only starts on server restart — breaking fresh pairs.
    try:
        camera = current_app.store.get_camera(camera_id)
        if camera and camera.recording_mode == "continuous":
            current_app.streaming.start_camera(camera_id)
    except Exception:
        pass  # Non-fatal: streaming can be started from dashboard

    return jsonify(result), 200
