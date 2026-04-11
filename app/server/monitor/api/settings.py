"""
Settings API — thin HTTP adapter.

All business logic delegated to SettingsService.

Endpoints:
  GET /settings       - current settings (login required)
  PUT /settings       - update settings (admin only)
  GET /settings/wifi  - current WiFi SSID + available networks (admin only)
  POST /settings/wifi - connect to a new WiFi network (admin only)
"""

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, login_required

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("", methods=["GET"])
@login_required
def get_settings():
    """Return current system settings."""
    result = current_app.settings_service.get_settings()
    return jsonify(result), 200


@settings_bp.route("", methods=["PUT"])
@admin_required
def update_settings():
    """Update system settings. Admin only."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    msg, status = current_app.settings_service.update_settings(
        data=data,
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if status != 200:
        return jsonify({"error": msg}), status
    return jsonify({"message": msg}), status


@settings_bp.route("/wifi", methods=["GET"])
@admin_required
def get_wifi():
    """Return current WiFi SSID and scan for available networks."""
    result = current_app.settings_service.get_wifi_status()
    return jsonify(result), 200


@settings_bp.route("/wifi", methods=["POST"])
@admin_required
def set_wifi():
    """Connect to a new WiFi network via nmcli."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    msg, status = current_app.settings_service.connect_wifi(
        ssid=data.get("ssid", ""),
        password=data.get("password", ""),
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if status != 200:
        return jsonify({"error": msg}), status
    return jsonify({"message": msg}), status
