"""
Settings API.

Endpoints:
  GET /settings       - current settings (login required)
  PUT /settings       - update settings (admin only)
  GET /settings/wifi  - current WiFi SSID + available networks (admin only)
  POST /settings/wifi - connect to a new WiFi network (admin only)

Settings stored in /data/config/settings.json.
Survives OTA updates (on data partition).
"""

import logging
import subprocess
import time

from flask import Blueprint, current_app, jsonify, request

from monitor.auth import admin_required, login_required

log = logging.getLogger("monitor.api.settings")

settings_bp = Blueprint("settings", __name__)

# Fields that can be updated via PUT
UPDATABLE_FIELDS = {
    "timezone",
    "storage_threshold_percent",
    "clip_duration_seconds",
    "session_timeout_minutes",
    "hostname",
}


@settings_bp.route("", methods=["GET"])
@login_required
def get_settings():
    """Return current system settings."""
    settings = current_app.store.get_settings()
    return jsonify(
        {
            "timezone": settings.timezone,
            "storage_threshold_percent": settings.storage_threshold_percent,
            "clip_duration_seconds": settings.clip_duration_seconds,
            "session_timeout_minutes": settings.session_timeout_minutes,
            "hostname": settings.hostname,
            "setup_completed": settings.setup_completed,
            "firmware_version": settings.firmware_version,
        }
    ), 200


@settings_bp.route("", methods=["PUT"])
@admin_required
def update_settings():
    """Update system settings. Admin only."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    # Validate: only known fields allowed
    unknown = set(data.keys()) - UPDATABLE_FIELDS
    if unknown:
        return jsonify({"error": f"Unknown fields: {', '.join(sorted(unknown))}"}), 400

    if not data:
        return jsonify({"error": "No updatable fields provided"}), 400

    # Validate field types
    errors = _validate_settings(data)
    if errors:
        return jsonify({"error": errors[0]}), 400

    settings = current_app.store.get_settings()

    for key, value in data.items():
        setattr(settings, key, value)

    current_app.store.save_settings(settings)

    audit = getattr(current_app, "audit", None)
    if audit:
        from flask import session

        audit.log_event(
            "SETTINGS_UPDATED",
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"updated: {', '.join(sorted(data.keys()))}",
        )

    return jsonify({"message": "Settings updated"}), 200


@settings_bp.route("/wifi", methods=["GET"])
@admin_required
def get_wifi():
    """Return current WiFi SSID and scan for available networks."""
    current_ssid = _get_current_ssid()
    networks = _scan_wifi_networks()
    return jsonify(
        {
            "current_ssid": current_ssid,
            "networks": networks,
        }
    ), 200


@settings_bp.route("/wifi", methods=["POST"])
@admin_required
def set_wifi():
    """Connect to a new WiFi network via nmcli."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    ssid = (data.get("ssid") or "").strip()
    password = data.get("password", "")

    if not ssid:
        return jsonify({"error": "ssid is required"}), 400
    if not password:
        return jsonify({"error": "password is required"}), 400

    ok, err = _connect_wifi(ssid, password)
    if ok:
        audit = getattr(current_app, "audit", None)
        if audit:
            from flask import session

            audit.log_event(
                "WIFI_CHANGED",
                user=session.get("username", ""),
                ip=request.remote_addr or "",
                detail=f"connected to: {ssid}",
            )
        return jsonify({"message": f"Connected to {ssid}"}), 200
    else:
        return jsonify({"error": err or "Connection failed"}), 500


def _get_current_ssid():
    """Get the SSID of the currently connected WiFi network."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "active,ssid", "device", "wifi"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0].lower() == "yes":
                return parts[1]
    except Exception as e:
        log.warning("Failed to get current SSID: %s", e)
    return ""


def _scan_wifi_networks():
    """Scan for available WiFi networks using nmcli."""
    try:
        # Trigger a rescan
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True,
            timeout=10,
        )
        time.sleep(2)

        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        networks = []
        seen = set()
        for line in result.stdout.strip().splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 3 and parts[0] and parts[0] not in seen:
                seen.add(parts[0])
                networks.append(
                    {
                        "ssid": parts[0],
                        "signal": int(parts[1]) if parts[1].isdigit() else 0,
                        "security": parts[2],
                    }
                )
        networks.sort(key=lambda n: n["signal"], reverse=True)
        return networks
    except Exception as e:
        log.warning("WiFi scan failed: %s", e)
        return []


def _connect_wifi(ssid, password):
    """Connect to a WiFi network. Returns (ok, error_message)."""
    try:
        result = subprocess.run(
            [
                "nmcli",
                "device",
                "wifi",
                "connect",
                ssid,
                "password",
                password,
                "ifname",
                "wlan0",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, ""
        err = result.stderr.strip() or result.stdout.strip()
        return False, err
    except subprocess.TimeoutExpired:
        return False, "Connection timed out"
    except Exception as e:
        return False, str(e)


def _validate_settings(data: dict) -> list[str]:
    """Validate setting values. Returns list of error messages."""
    errors = []

    if "storage_threshold_percent" in data:
        val = data["storage_threshold_percent"]
        if not isinstance(val, int) or val < 50 or val > 99:
            errors.append(
                "storage_threshold_percent must be an integer between 50 and 99"
            )

    if "clip_duration_seconds" in data:
        val = data["clip_duration_seconds"]
        if not isinstance(val, int) or val < 30 or val > 600:
            errors.append("clip_duration_seconds must be an integer between 30 and 600")

    if "session_timeout_minutes" in data:
        val = data["session_timeout_minutes"]
        if not isinstance(val, int) or val < 5 or val > 1440:
            errors.append(
                "session_timeout_minutes must be an integer between 5 and 1440"
            )

    if "hostname" in data:
        val = data["hostname"]
        if not isinstance(val, str) or len(val) < 1 or len(val) > 63:
            errors.append("hostname must be a string between 1 and 63 characters")

    if "timezone" in data:
        val = data["timezone"]
        if not isinstance(val, str) or len(val) < 1 or "/" not in val:
            errors.append(
                "timezone must be a valid timezone string (e.g., Europe/Dublin)"
            )

    return errors
