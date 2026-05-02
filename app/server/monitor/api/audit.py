# REQ: SWR-009; RISK: RISK-020; SEC: SC-008, SC-020; TEST: TC-017
"""
Audit log API — read and clear the security audit trail.

Powers the dashboard's "recent activity" log teaser (ADR-0018 Slice 3)
and the Settings > Security audit view. Write access stays private to
the services that emit events (pairing, OTA, user auth, clip delete);
the HTTP layer is admin-gated so a compromised low-priv session can't
exfiltrate login-failure patterns or erase the audit trail.

Endpoints:
  GET    /events  - most recent audit events (admin only)
  DELETE /events  - truncate the audit log (admin only; writes sentinel first)

Query params (GET only):
  limit       - 1..200, default 50
  event_type  - filter by exact event name (optional)
"""

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect

audit_bp = Blueprint("audit", __name__)


@audit_bp.route("/events", methods=["GET"])
@admin_required
def list_events():
    """Return recent audit events, newest-first."""
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))

    event_type = request.args.get("event_type", "").strip()

    try:
        events = current_app.audit.get_events(limit=limit, event_type=event_type)
    except Exception:  # pragma: no cover - defensive: never crash status strip
        events = []

    return jsonify({"events": events, "count": len(events)})


@audit_bp.route("/events", methods=["DELETE"])
@admin_required
@csrf_protect
def clear_events():
    """Truncate the audit log (admin only).

    Writes an AUDIT_LOG_CLEARED sentinel before truncating so chain of
    custody is preserved. Returns 200 with cleared=true on success.
    """
    ip = request.remote_addr or ""
    user = session.get("username", "")

    try:
        current_app.audit.clear_events(user=user, ip=ip)
    except Exception:  # pragma: no cover
        return jsonify({"cleared": False, "error": "internal error"}), 500

    return jsonify({"cleared": True})
