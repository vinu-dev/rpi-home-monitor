"""
Audit log API — read-only access to the security audit trail.

Powers the dashboard's "recent activity" log teaser (ADR-0018 Slice 3)
and the Settings > Security audit view. Write access stays private to
the services that emit events (pairing, OTA, user auth, clip delete);
the HTTP layer is read-only and admin-gated so a compromised low-priv
session can't exfiltrate login-failure patterns.

Endpoints:
  GET /events         - most recent audit events (admin only)

Query params:
  limit       - 1..200, default 50
  event_type  - filter by exact event name (optional)
"""

from flask import Blueprint, current_app, jsonify, request

from monitor.auth import admin_required

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
