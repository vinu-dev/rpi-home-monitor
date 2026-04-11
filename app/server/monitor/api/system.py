"""
System health and info API.

Endpoints:
  GET /system/health  - CPU temp, CPU%, RAM%, disk usage, warnings
  GET /system/info    - firmware version, uptime, hostname
"""

from flask import Blueprint, current_app, jsonify

from monitor.auth import login_required
from monitor.services.health import get_health_summary, get_uptime

system_bp = Blueprint("system", __name__)


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
    return jsonify(
        {
            "hostname": settings.hostname,
            "firmware_version": settings.firmware_version,
            "uptime": uptime,
        }
    ), 200
