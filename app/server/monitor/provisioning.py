"""
WiFi hotspot provisioning blueprint — thin HTTP adapter.

All business logic delegated to ProvisioningService.
Routes handle HTTP parsing and return JSON responses.
"""

import logging

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
)

log = logging.getLogger("monitor.provisioning")

provisioning_bp = Blueprint("provisioning", __name__)


@provisioning_bp.route("/status", methods=["GET"])
def setup_status():
    """Return current setup state."""
    result = current_app.provisioning_service.get_status()
    return jsonify(result), 200


@provisioning_bp.route("/wifi/scan", methods=["GET"])
def wifi_scan():
    """Scan for available WiFi networks."""
    networks, err, status = current_app.provisioning_service.scan_wifi()
    if err:
        return jsonify({"error": err}), status
    return jsonify({"networks": networks}), 200


@provisioning_bp.route("/wifi/save", methods=["POST"])
def wifi_save():
    """Save WiFi credentials for later use."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    msg, status = current_app.provisioning_service.save_wifi_credentials(
        ssid=data.get("ssid", ""),
        password=data.get("password", ""),
    )
    if status != 200:
        return jsonify({"error": msg}), status
    return jsonify({"message": msg}), status


@provisioning_bp.route("/admin", methods=["POST"])
def set_admin_password():
    """Set a new admin password."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    msg, status = current_app.provisioning_service.set_admin_password(
        password=data.get("password", ""),
    )
    if status != 200:
        return jsonify({"error": msg}), status
    return jsonify({"message": msg}), status


@provisioning_bp.route("/complete", methods=["POST"])
def setup_complete():
    """Apply all settings and finish setup."""
    result, err, status = current_app.provisioning_service.complete_setup()
    if err:
        return jsonify({"error": err}), status
    return jsonify(result), status


@provisioning_bp.route("/wizard", methods=["GET"])
def setup_wizard():
    """Serve the setup wizard HTML page."""
    from monitor.services.provisioning_service import SERVER_HOSTNAME

    return render_template("setup.html", hostname=f"{SERVER_HOSTNAME}.local")
