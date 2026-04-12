"""
System health and info API.

Endpoints:
  GET  /system/health              - CPU temp, CPU%, RAM%, disk usage, warnings
  GET  /system/info                - firmware version, uptime, hostname, OS version
  GET  /system/tailscale           - Tailscale VPN status
  POST /system/tailscale/connect   - Start Tailscale, return auth URL if needed
  POST /system/tailscale/disconnect - Stop Tailscale
  POST /system/factory-reset       - Wipe all data and return to first-boot state
"""

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, login_required
from monitor.services.health import get_health_summary, get_uptime

system_bp = Blueprint("system", __name__)


def _read_os_release():
    """Read /etc/os-release into a dict. Returns empty dict on failure."""
    try:
        with open("/etc/os-release") as f:
            result = {}
            for line in f:
                line = line.strip()
                if "=" in line:
                    key, _, value = line.partition("=")
                    result[key] = value.strip('"')
            return result
    except OSError:
        return {}


@system_bp.route("/health", methods=["GET"])
@login_required
def health():
    """Return system health metrics."""
    data_dir = current_app.config.get("DATA_DIR", "/data")
    summary = get_health_summary(data_dir)
    return jsonify(summary), 200


@system_bp.route("/info", methods=["GET"])
@login_required
def info():
    """Return system info."""
    settings = current_app.store.get_settings()
    uptime = get_uptime()
    os_info = _read_os_release()
    return jsonify(
        {
            "hostname": settings.hostname,
            "firmware_version": settings.firmware_version,
            "uptime": uptime,
            "os_name": os_info.get("PRETTY_NAME", "Unknown"),
            "os_version": os_info.get("VERSION_ID", ""),
            "os_build": os_info.get("BUILD_ID", ""),
            "os_variant": os_info.get("VARIANT_ID", ""),
        }
    ), 200


# ---------------------------------------------------------------------------
# Tailscale VPN management
# ---------------------------------------------------------------------------


@system_bp.route("/tailscale", methods=["GET"])
@login_required
def tailscale_status():
    """Get Tailscale VPN status."""
    ts = current_app.tailscale_service
    return jsonify(ts.get_status()), 200


@system_bp.route("/tailscale/connect", methods=["POST"])
@admin_required
def tailscale_connect():
    """Start Tailscale. Returns auth URL if login is needed. Admin only."""
    ts = current_app.tailscale_service
    auth_url, err = ts.connect()
    if err:
        return jsonify({"error": err}), 500

    if auth_url:
        return jsonify(
            {"auth_url": auth_url, "message": "Visit URL to authenticate"}
        ), 200

    return jsonify({"message": "Tailscale connected"}), 200


@system_bp.route("/tailscale/disconnect", methods=["POST"])
@admin_required
def tailscale_disconnect():
    """Stop Tailscale (keeps authentication). Admin only."""
    ts = current_app.tailscale_service
    ok, err = ts.disconnect()
    if not ok:
        return jsonify({"error": err}), 500

    return jsonify({"message": "Tailscale disconnected"}), 200


# ---------------------------------------------------------------------------
# Factory reset
# ---------------------------------------------------------------------------


@system_bp.route("/factory-reset", methods=["POST"])
@admin_required
def factory_reset():
    """Wipe all data and return to first-boot state. Admin only.

    Accepts optional JSON body: {"keep_recordings": true}
    """
    body = request.get_json(silent=True) or {}
    keep_recordings = bool(body.get("keep_recordings", False))

    user = session.get("user", "")
    ip = request.remote_addr or ""

    svc = current_app.factory_reset_service
    msg, status = svc.execute_reset(
        keep_recordings=keep_recordings,
        requesting_user=user,
        requesting_ip=ip,
    )
    return jsonify({"message": msg}), status
