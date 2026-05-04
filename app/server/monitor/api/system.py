# REQ: SWR-024, SWR-032, SWR-020, SWR-018, SWR-068, SWR-070; RISK: RISK-012, RISK-015, RISK-017, RISK-006, RISK-020, RISK-026; SEC: SC-012, SC-020, SC-004, SC-006, SC-025; TEST: TC-023, TC-029, TC-010, TC-015, TC-055
"""
System health and info API.

Endpoints:
  GET  /system/health                  - CPU temp, CPU%, RAM%, disk usage, warnings
  GET  /system/time/health             - derived server/camera time integrity (admin only)
  POST /system/time/resync             - restart timesyncd on server or queue camera resync
  GET  /system/info                    - firmware version, uptime, hostname, OS version
  GET  /system/tailscale               - Tailscale VPN status + config
  POST /system/tailscale/connect       - Start Tailscale, return auth URL if needed
  POST /system/tailscale/disconnect    - Stop Tailscale
  POST /system/tailscale/enable        - Enable tailscaled daemon
  POST /system/tailscale/disable       - Disable tailscaled daemon
  POST /system/tailscale/apply-config  - Apply saved Tailscale settings
  POST /system/factory-reset           - Wipe all data and return to first-boot state
  POST /system/backup/export           - Download a signed configuration bundle
  POST /system/backup/preview          - Validate + preview a backup bundle
  POST /system/backup/import           - Restore a backup bundle
  POST /system/diagnostics/export      - Download a diagnostics tarball
  GET  /system/backup/snapshots        - List rollback snapshots created on import
"""

import time
from datetime import datetime
from io import BytesIO

from flask import Blueprint, current_app, jsonify, request, send_file, session

from monitor.auth import admin_required, csrf_protect, login_required
from monitor.services.config_backup_service import ConfigBackupError
from monitor.services.diagnostics_bundle import DiagnosticsBundleError
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


def _bool_from_request(value, default=False):
    """Parse booleans from JSON/form data."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _log_backup_audit(event: str, *, user: str, ip: str, detail: str) -> None:
    """Best-effort audit logging for backup actions."""
    audit = getattr(current_app, "audit", None)
    if not audit:
        return
    try:
        audit.log_event(event, user=user, ip=ip, detail=detail)
    except Exception:
        current_app.logger.debug("Backup audit log failed for %s", event)


def _log_time_audit(*, user: str, ip: str, detail: str) -> None:
    """Best-effort audit logging for time-related admin actions."""
    audit = getattr(current_app, "audit", None)
    if not audit:
        return
    try:
        audit.log_event("TIME_RESYNC_REQUESTED", user=user, ip=ip, detail=detail)
    except Exception:
        current_app.logger.debug("Time resync audit log failed for %s", detail)


def _backup_scope_from_body(body: dict | None) -> dict:
    """Normalise export-scope booleans from a JSON request body."""
    body = body or {}
    scope = body.get("scope") if isinstance(body.get("scope"), dict) else {}
    return {
        "users": _bool_from_request(scope.get("users", body.get("users", True)), True),
        "cameras": _bool_from_request(
            scope.get("cameras", body.get("cameras", True)),
            True,
        ),
        "settings": _bool_from_request(
            scope.get("settings", body.get("settings", True)),
            True,
        ),
        "include_user_credentials": _bool_from_request(
            body.get("include_user_credentials", False),
            False,
        ),
        "include_camera_trust": _bool_from_request(
            body.get("include_camera_trust", True),
            True,
        ),
        "include_webhook_secrets": _bool_from_request(
            body.get("include_webhook_secrets", False),
            False,
        ),
        "include_tailscale_auth_key": _bool_from_request(
            body.get("include_tailscale_auth_key", False),
            False,
        ),
    }


def _backup_restore_scope(form) -> dict:
    """Normalise explicit restore-scope booleans from a multipart upload."""
    scope = {}
    for key in ("users", "cameras", "settings", "camera_trust"):
        if key in form:
            scope[key] = _bool_from_request(form.get(key), False)
    return scope


def _scope_detail(scope: dict) -> str:
    """Format enabled scope names for audit detail strings."""
    enabled = [name for name, value in scope.items() if value]
    return ",".join(enabled) if enabled else "none"


def _read_uploaded_bundle() -> tuple[bytes, str]:
    """Read the uploaded backup bundle from multipart form data."""
    if "file" not in request.files:
        raise ConfigBackupError("No backup bundle provided", reason="missing_bundle")
    file = request.files["file"]
    filename = file.filename or "backup.hmb"
    payload = file.read()
    if not payload:
        raise ConfigBackupError(
            "Uploaded backup bundle is empty", reason="empty_bundle"
        )
    return payload, filename


def _read_passphrase(source: dict | None) -> str:
    """Read and validate the backup passphrase from request data."""
    source = source or {}
    passphrase = source.get("passphrase")
    if not isinstance(passphrase, str) or not passphrase.strip():
        raise ConfigBackupError(
            "Passphrase is required",
            reason="missing_passphrase",
        )
    return passphrase


def _backup_error_response(exc: ConfigBackupError):
    """Return a consistent JSON error payload for backup routes."""
    return jsonify({"error": str(exc), "reason": exc.reason}), exc.status_code


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


@system_bp.route("/time/health", methods=["GET"])
@admin_required
def time_health():
    """Return the current derived time-health payload. Admin only."""
    return jsonify(current_app.time_health_service.compute_health()), 200


@system_bp.route("/time/resync", methods=["POST"])
@admin_required
@csrf_protect
def time_resync():
    """Restart timesyncd on the server or queue a camera-side resync."""
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "JSON body required"}), 400
    target = data.get("target")
    if not isinstance(target, str) or not target.strip():
        return jsonify({"error": "target is required"}), 400

    message, status, should_audit = current_app.time_health_service.request_resync(
        target
    )
    if status != 200:
        return jsonify({"error": message}), status
    if should_audit:
        _log_time_audit(
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"target={target.strip()}",
        )
    return jsonify({"message": message}), 200


@system_bp.route("/info", methods=["GET"])
@login_required
def info():
    """Return system info."""
    settings = current_app.store.get_settings()
    uptime = get_uptime()
    os_info = _read_os_release()
    # Live read of release version from /etc/os-release via the shared
    # helper — the persisted Settings.firmware_version is legacy plumbing
    # (see docs/architecture/versioning.md §C). Note: we already parse
    # /etc/os-release locally in this module via _read_os_release(); we
    # still defer to the helper to keep ONE read path and ONE caching
    # policy across the codebase.
    from monitor.release_version import release_version

    return jsonify(
        {
            "hostname": settings.hostname,
            "firmware_version": release_version(),
            "uptime": uptime,
            "os_name": os_info.get("PRETTY_NAME", "Unknown"),
            "os_version": os_info.get("VERSION_ID", ""),
            "os_build": os_info.get("BUILD_ID", ""),
            "os_variant": os_info.get("VARIANT_ID", ""),
        }
    ), 200


@system_bp.route("/diagnostics/export", methods=["POST"])
@admin_required
@csrf_protect
def diagnostics_export():
    """Build and download a diagnostics tarball. Admin only."""
    user = session.get("username", "")
    ip = request.remote_addr or ""
    session_id = session.get("sid", "")
    service = current_app.diagnostics_service

    allowed, retry_after = service.check_rate_limit(session_id)
    if not allowed:
        response = jsonify(
            {
                "error": "diagnostics_export_rate_limited",
                "retry_after_seconds": retry_after,
            }
        )
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    try:
        result = service.collect_sections(requested_by=user, requested_ip=ip)
    except DiagnosticsBundleError as exc:
        response = jsonify(exc.payload)
        if exc.retry_after_seconds:
            response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response, exc.status_code

    try:
        archive_stream = service.open_archive_stream(result)
        response = send_file(
            archive_stream,
            mimetype="application/gzip",
            as_attachment=True,
            download_name=result.download_name,
        )
    except Exception:
        if "archive_stream" in locals():
            archive_stream.close()
        else:
            service.cleanup(result.run_id)
        raise

    return response


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


@system_bp.route("/backup/export", methods=["POST"])
@admin_required
@csrf_protect
def backup_export():
    """Export a signed configuration backup bundle. Admin only."""
    body = request.get_json(silent=True) or {}
    user = session.get("username", "")
    ip = request.remote_addr or ""

    try:
        passphrase = _read_passphrase(body)
        options = _backup_scope_from_body(body)
        filename, bundle_bytes, preview = (
            current_app.config_backup_service.export_bundle(
                passphrase=passphrase,
                options=options,
            )
        )
    except ConfigBackupError as exc:
        _log_backup_audit(
            "CONFIG_BACKUP_EXPORT_REJECTED",
            user=user,
            ip=ip,
            detail=f"reason={exc.reason}",
        )
        return _backup_error_response(exc)

    _log_backup_audit(
        "CONFIG_BACKUP_EXPORTED",
        user=user,
        ip=ip,
        detail=(
            f"scope={_scope_detail(preview.get('scope', {}))}; "
            f"users={preview.get('counts', {}).get('users', 0)}; "
            f"cameras={preview.get('counts', {}).get('cameras', 0)}"
        ),
    )
    return send_file(
        BytesIO(bundle_bytes),
        mimetype="application/vnd.home-monitor.backup+json",
        as_attachment=True,
        download_name=filename,
    )


@system_bp.route("/backup/preview", methods=["POST"])
@admin_required
@csrf_protect
def backup_preview():
    """Validate and preview a backup bundle before restore. Admin only."""
    user = session.get("username", "")
    ip = request.remote_addr or ""

    try:
        bundle_bytes, filename = _read_uploaded_bundle()
        passphrase = _read_passphrase(request.form)
        preview = current_app.config_backup_service.preview_bundle(
            bundle_bytes,
            passphrase=passphrase,
        )
    except ConfigBackupError as exc:
        _log_backup_audit(
            "CONFIG_BACKUP_PREVIEW_REJECTED",
            user=user,
            ip=ip,
            detail=f"reason={exc.reason}",
        )
        return _backup_error_response(exc)

    _log_backup_audit(
        "CONFIG_BACKUP_PREVIEWED",
        user=user,
        ip=ip,
        detail=f"filename={filename}; scope={_scope_detail(preview.get('scope', {}))}",
    )
    return jsonify({"filename": filename, "preview": preview}), 200


@system_bp.route("/backup/import", methods=["POST"])
@admin_required
@csrf_protect
def backup_import():
    """Restore a validated backup bundle. Admin only."""
    user = session.get("username", "")
    ip = request.remote_addr or ""

    try:
        bundle_bytes, filename = _read_uploaded_bundle()
        passphrase = _read_passphrase(request.form)
        restore_options = _backup_restore_scope(request.form)
        _log_backup_audit(
            "CONFIG_BACKUP_IMPORT_ATTEMPT",
            user=user,
            ip=ip,
            detail=(f"filename={filename}; scope={_scope_detail(restore_options)}"),
        )
        result = current_app.config_backup_service.import_bundle(
            bundle_bytes,
            passphrase=passphrase,
            restore_options=restore_options,
        )
    except ConfigBackupError as exc:
        _log_backup_audit(
            "CONFIG_BACKUP_IMPORT_REJECTED",
            user=user,
            ip=ip,
            detail=f"reason={exc.reason}",
        )
        return _backup_error_response(exc)

    _log_backup_audit(
        "CONFIG_BACKUP_IMPORTED",
        user=user,
        ip=ip,
        detail=(
            f"filename={filename}; "
            f"snapshot={result.get('snapshot', {}).get('id', '')}; "
            f"scope={_scope_detail({name: True for name in result.get('restored_components', [])})}"
        ),
    )
    return jsonify(result), 200


@system_bp.route("/backup/snapshots", methods=["GET"])
@admin_required
def backup_snapshots():
    """List rollback snapshots created during restore. Admin only."""
    return jsonify(
        {"snapshots": current_app.config_backup_service.list_snapshots()}
    ), 200
