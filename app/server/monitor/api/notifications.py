"""
Notification center API — pending pull / mark-seen / per-user prefs.

Implements ADR-0027 (#121, #128). The persistent triage surface is
``/api/v1/alerts/*``; this layer is the timely-delivery side that
the polling browser client consumes to fire OS-level Web
Notifications.

Endpoints:

  GET    /pending?since=<iso>&limit=<n>    — surfaceable alerts
  POST   /seen                              — mark delivered
  GET    /prefs                             — current user's prefs
  PUT    /prefs                             — update current user's prefs

All routes are session-authenticated. The role-aware permission gate
is implicit in the policy service: viewers see only the cameras
their account is opted into; admins see only their own opted-in
cameras (notifications are personal — admin-vs-viewer doesn't
expand the camera set).
"""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import csrf_protect, login_required

log = logging.getLogger("monitor.api.notifications")

notifications_bp = Blueprint("notifications", __name__)


def _current_user() -> str:
    return session.get("username", "") or ""


@notifications_bp.route("/pending", methods=["GET"])
@login_required
def list_pending():
    """Surfaceable motion notifications for the current user.

    Newest first. ``since`` defaults to the user's
    ``last_notification_seen_at`` so a polling client doesn't
    re-receive what it already delivered. ``limit`` clamped to
    [1, 100].
    """
    user = _current_user()
    since = (request.args.get("since") or "").strip() or None
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 100))

    svc = current_app.notification_policy
    alerts = svc.select_for_user(user=user, since=since, limit=limit)
    return jsonify({"alerts": alerts, "count": len(alerts)})


@notifications_bp.route("/seen", methods=["POST"])
@login_required
@csrf_protect
def mark_seen():
    """Mark a list of motion alert ids as delivered to this user.

    Idempotent: re-marking already-seen alerts is a no-op.
    Body: ``{"alert_ids": ["motion:<event_id>", ...]}``.
    """
    user = _current_user()
    if not user:
        return jsonify({"marked": 0, "error": "no session user"}), 401
    body = request.get_json(silent=True) or {}
    alert_ids = body.get("alert_ids") or []
    if not isinstance(alert_ids, list):
        return jsonify({"marked": 0, "error": "alert_ids must be a list"}), 400
    marked = current_app.notification_policy.mark_seen(user=user, alert_ids=alert_ids)
    return jsonify({"marked": marked})


@notifications_bp.route("/prefs", methods=["GET"])
@login_required
def get_prefs():
    """Return the current user's notification preferences."""
    user = _current_user()
    return jsonify({"prefs": current_app.notification_policy.get_prefs(user)})


@notifications_bp.route("/prefs", methods=["PUT"])
@login_required
@csrf_protect
def update_prefs():
    """Update the current user's notification preferences.

    Validates per ADR-0027: bounded integer ranges, boolean type
    enforcement, partial-update semantics (omitted keys leave the
    server-side value untouched; explicit ``null`` in a per-camera
    override removes that camera-specific override).
    """
    user = _current_user()
    if not user:
        return jsonify({"error": "no session user"}), 401
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "body must be an object"}), 400
    new_prefs, err = current_app.notification_policy.update_prefs(
        user=user, payload=body
    )
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"prefs": new_prefs})
