# REQ: SWR-024; RISK: RISK-012; SEC: SC-012; TEST: TC-023
"""
Settings API — thin HTTP adapter.

All business logic delegated to SettingsService.

Endpoints:
  GET  /settings       - current settings (login required)
  PUT  /settings       - update settings (admin only)
  GET  /settings/wifi  - current WiFi SSID + available networks (admin only)
  POST /settings/wifi  - connect to a new WiFi network (admin only)
  GET  /settings/time  - current timezone + NTP state + system time (login required)
  POST /settings/time  - set system clock (admin only, ntp_mode=manual)
"""

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect, login_required

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("", methods=["GET"])
@login_required
def get_settings():
    """Return current system settings."""
    result = current_app.settings_service.get_settings()
    return jsonify(result), 200


@settings_bp.route("", methods=["PUT"])
@admin_required
@csrf_protect
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


@settings_bp.route("/offsite-backup", methods=["GET"])
@admin_required
def get_offsite_backup_settings():
    """Return offsite-backup configuration and queue status. Admin only."""
    result = current_app.offsite_backup_service.get_settings_status()
    return jsonify(result), 200


@settings_bp.route("/offsite-backup", methods=["PUT"])
@admin_required
@csrf_protect
def update_offsite_backup_settings():
    """Update offsite-backup configuration. Admin only."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    msg, status = current_app.offsite_backup_service.update_config(
        payload=data,
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if status != 200:
        return jsonify({"error": msg}), status
    return jsonify({"message": msg}), status


@settings_bp.route("/offsite-backup/test-connection", methods=["POST"])
@admin_required
@csrf_protect
def test_offsite_backup_connection():
    """Test offsite-backup connectivity with the supplied or saved settings."""
    data = request.get_json(silent=True)
    if data is None:
        data = {}

    msg, status = current_app.offsite_backup_service.test_connection(
        payload=data,
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if status != 200:
        return jsonify({"error": msg}), status
    return jsonify({"message": msg}), status


@settings_bp.route("/time", methods=["GET"])
@login_required
def get_time():
    """Return current timezone, NTP state, and system time (ADR-0019)."""
    result = current_app.settings_service.get_time_status()
    return jsonify(result), 200


@settings_bp.route("/time", methods=["POST"])
@admin_required
@csrf_protect
def set_manual_time():
    """Set the system clock manually. Requires ntp_mode=manual."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    msg, status = current_app.settings_service.set_manual_time(
        iso_time=data.get("time", ""),
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
@csrf_protect
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
