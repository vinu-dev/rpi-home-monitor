"""
Settings API.

Endpoints:
  GET /settings  - current settings (login required)
  PUT /settings  - update settings (admin only)

Settings stored in /data/config/settings.json.
Survives OTA updates (on data partition).
"""
from flask import Blueprint, current_app, jsonify, request

from monitor.auth import admin_required, login_required

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
    return jsonify({
        "timezone": settings.timezone,
        "storage_threshold_percent": settings.storage_threshold_percent,
        "clip_duration_seconds": settings.clip_duration_seconds,
        "session_timeout_minutes": settings.session_timeout_minutes,
        "hostname": settings.hostname,
        "setup_completed": settings.setup_completed,
        "firmware_version": settings.firmware_version,
    }), 200


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


def _validate_settings(data: dict) -> list[str]:
    """Validate setting values. Returns list of error messages."""
    errors = []

    if "storage_threshold_percent" in data:
        val = data["storage_threshold_percent"]
        if not isinstance(val, int) or val < 50 or val > 99:
            errors.append("storage_threshold_percent must be an integer between 50 and 99")

    if "clip_duration_seconds" in data:
        val = data["clip_duration_seconds"]
        if not isinstance(val, int) or val < 30 or val > 600:
            errors.append("clip_duration_seconds must be an integer between 30 and 600")

    if "session_timeout_minutes" in data:
        val = data["session_timeout_minutes"]
        if not isinstance(val, int) or val < 5 or val > 1440:
            errors.append("session_timeout_minutes must be an integer between 5 and 1440")

    if "hostname" in data:
        val = data["hostname"]
        if not isinstance(val, str) or len(val) < 1 or len(val) > 63:
            errors.append("hostname must be a string between 1 and 63 characters")

    if "timezone" in data:
        val = data["timezone"]
        if not isinstance(val, str) or len(val) < 1 or "/" not in val:
            errors.append("timezone must be a valid timezone string (e.g., Europe/Dublin)")

    return errors
