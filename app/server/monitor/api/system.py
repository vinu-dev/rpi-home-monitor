"""
System health and info API.

Endpoints:
  GET  /system/health                  - CPU temp, CPU%, RAM%, disk usage, warnings
  GET  /system/info                    - firmware version, uptime, hostname, OS version
  GET  /system/tailscale               - Tailscale VPN status + config
  POST /system/tailscale/connect       - Start Tailscale, return auth URL if needed
  POST /system/tailscale/disconnect    - Stop Tailscale
  POST /system/tailscale/enable        - Enable tailscaled daemon
  POST /system/tailscale/disable       - Disable tailscaled daemon
  POST /system/tailscale/apply-config  - Apply saved Tailscale settings
  POST /system/factory-reset           - Wipe all data and return to first-boot state
"""

import time
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect, login_required
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


@system_bp.route("/time", methods=["GET"])
@login_required
def time_now():
    """Return the server's current wall-clock time.

    Used by the dashboard top-bar clock so the displayed time matches
    what ends up in audit logs / motion event timestamps. The
    dashboard fetches this once on load, computes offset from the
    client clock, and ticks locally every second — re-syncing every
    five minutes to avoid drift.
    """
    now = datetime.now().astimezone()
    # Local-time ISO without microseconds + offset (e.g.
    # "2026-04-22T07:34:02+01:00"). Clients can Date.parse() it
    # directly.
    iso = now.replace(microsecond=0).isoformat()
    return jsonify(
        {
            "iso": iso,
            "unix": int(time.time()),
            "tz": now.tzname() or "UTC",
            "tz_offset_seconds": int(now.utcoffset().total_seconds())
            if now.utcoffset()
            else 0,
        }
    )


@system_bp.route("/health", methods=["GET"])
@login_required
def health():
    """Return raw system health metrics.

    Raw CPU/RAM/temp/disk numbers. The dashboard does **not** render these
    directly (ADR-0018 rule: raw metrics belong on /diagnostics, derived
    state belongs on the dashboard). The future /diagnostics page will
    surface them; for now the dashboard uses /system/summary instead.
    """
    data_dir = current_app.config.get("DATA_DIR", "/data")
    summary = get_health_summary(data_dir)
    return jsonify(summary), 200


@system_bp.route("/summary", methods=["GET"])
@login_required
def summary():
    """Return the ADR-0018 Tier-1 dashboard status-strip payload.

    Derived state only — ``{state, summary, details, deep_link}``.
    """
    result = current_app.system_summary_service.compute_summary()
    return jsonify(result), 200


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
    """Get Tailscale VPN status plus persisted config."""
    ts = current_app.tailscale_service
    status = ts.get_status()

    # Merge persisted config (never expose the auth key value)
    settings = current_app.store.get_settings()
    status["config"] = {
        "enabled": settings.tailscale_enabled,
        "auto_connect": settings.tailscale_auto_connect,
        "accept_routes": settings.tailscale_accept_routes,
        "ssh": settings.tailscale_ssh,
        "has_auth_key": bool(settings.tailscale_auth_key),
    }

    return jsonify(status), 200


@system_bp.route("/tailscale/connect", methods=["POST"])
@admin_required
@csrf_protect
def tailscale_connect():
    """Start Tailscale with saved flags. Returns auth URL if needed. Admin only."""
    ts = current_app.tailscale_service
    settings = current_app.store.get_settings()
    auth_url, err = ts.connect(
        accept_routes=settings.tailscale_accept_routes,
        ssh=settings.tailscale_ssh,
        authkey=settings.tailscale_auth_key,
    )
    if err:
        return jsonify({"error": err}), 500

    if auth_url:
        return jsonify(
            {"auth_url": auth_url, "message": "Visit URL to authenticate"}
        ), 200

    return jsonify({"message": "Tailscale connected"}), 200


@system_bp.route("/tailscale/disconnect", methods=["POST"])
@admin_required
@csrf_protect
def tailscale_disconnect():
    """Stop Tailscale (keeps authentication). Admin only."""
    ts = current_app.tailscale_service
    ok, err = ts.disconnect()
    if not ok:
        return jsonify({"error": err}), 500

    return jsonify({"message": "Tailscale disconnected"}), 200


@system_bp.route("/tailscale/enable", methods=["POST"])
@admin_required
@csrf_protect
def tailscale_enable():
    """Enable and start tailscaled daemon. Admin only."""
    ts = current_app.tailscale_service
    ok, err = ts.enable()
    if not ok:
        return jsonify({"error": err}), 500
    return jsonify({"message": "Tailscale daemon enabled"}), 200


@system_bp.route("/tailscale/disable", methods=["POST"])
@admin_required
@csrf_protect
def tailscale_disable():
    """Disable and stop tailscaled daemon. Admin only."""
    ts = current_app.tailscale_service
    ok, err = ts.disable()
    if not ok:
        return jsonify({"error": err}), 500
    return jsonify({"message": "Tailscale daemon disabled"}), 200


@system_bp.route("/tailscale/apply-config", methods=["POST"])
@admin_required
@csrf_protect
def tailscale_apply_config():
    """Apply saved Tailscale settings (enable/disable, auto-connect). Admin only."""
    ts = current_app.tailscale_service
    auth_url, err = ts.apply_config()
    if err:
        return jsonify({"error": err}), 500

    if auth_url:
        return jsonify(
            {"auth_url": auth_url, "message": "Visit URL to authenticate"}
        ), 200

    return jsonify({"message": "Tailscale config applied"}), 200


# ---------------------------------------------------------------------------
# Factory reset
# ---------------------------------------------------------------------------


@system_bp.route("/factory-reset", methods=["POST"])
@admin_required
@csrf_protect
def factory_reset():
    """Wipe all data and return to first-boot state. Admin only.

    Accepts optional JSON body: {"keep_recordings": true}
    """
    body = request.get_json(silent=True) or {}
    keep_recordings = bool(body.get("keep_recordings", False))

    user = session.get("username", "")
    ip = request.remote_addr or ""

    svc = current_app.factory_reset_service
    msg, status = svc.execute_reset(
        keep_recordings=keep_recordings,
        requesting_user=user,
        requesting_ip=ip,
    )
    return jsonify({"message": msg}), status
