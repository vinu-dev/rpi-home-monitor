"""
Camera pairing API.

Endpoints:
  POST /cameras/<id>/pair   - initiate pairing, generate PIN (admin)
  POST /cameras/<id>/unpair - revoke cert, reset pairing (admin)
  POST /pair/exchange        - camera trades PIN for certs (no auth — PIN is auth)

The exchange endpoint intentionally has no session auth — the 6-digit PIN
(rate-limited, 5-min expiry) is the authentication mechanism for the camera.
Routes are thin — all orchestration is in PairingService.
"""

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required

pairing_bp = Blueprint("pairing", __name__)


@pairing_bp.route("/cameras/<camera_id>/pair", methods=["POST"])
@admin_required
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
        return jsonify({"error": error}), status
    return jsonify({"pin": pin, "expires_in": 300}), 200


@pairing_bp.route("/cameras/<camera_id>/unpair", methods=["POST"])
@admin_required
def unpair_camera(camera_id):
    """Unpair a camera and revoke its certificate. Admin only."""
    error, status = current_app.pairing_service.unpair(
        camera_id,
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"message": "Camera unpaired"}), 200


@pairing_bp.route("/pair/register", methods=["POST"])
def register_camera():
    """Camera self-registers as pending on the server.

    No session auth — camera calls this before pairing to appear in
    the dashboard. The server creates a pending entry if it doesn't
    already exist.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    camera_id = data.get("camera_id", "")
    if not camera_id:
        return jsonify({"error": "camera_id required"}), 400

    current_app.discovery_service.report_camera(
        camera_id=camera_id,
        ip=request.remote_addr or "",
        firmware_version=data.get("firmware_version", ""),
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

    result, error, status = current_app.pairing_service.exchange_certs(pin, camera_id)
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), 200
