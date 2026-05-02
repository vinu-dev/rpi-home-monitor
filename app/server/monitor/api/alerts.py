# REQ: SWR-017, SWR-033; RISK: RISK-005, RISK-016; SEC: SC-008, SC-015; TEST: TC-014, TC-031
"""
Alert center API — list, mark-read, unread-count.

Implements ADR-0024 §"API". All routes are session-authenticated; the
service-layer permission gate (admin sees everything; viewer sees only
fault- and motion-derived alerts) is enforced inside
``AlertCenterService.list_alerts()``, not here. This keeps the
defence-in-depth pattern from issue #148 — server-side filter is the
source of truth, the UI does not render stale-but-ungated rows.

Endpoints:
  GET    /                  — list alerts (filters via query params)
  POST   /<alert_id>/read   — mark a single alert as read for this user
  POST   /mark-all-read     — bulk mark, respecting the same filters as GET
  GET    /unread-count      — cheap badge count

Query params on GET / and POST /mark-all-read (both share the filter set):
  source       — "fault" | "audit" | "motion"           (optional)
  severity     — "info" | "warning" | "error" | "critical"
                 (treated as "at least this severe")    (optional)
  unread_only  — "1" / "true" / etc. (GET only)
  limit        — 1..200, default 50 (GET only)
  before       — ISO-8601 timestamp; only alerts strictly older
  sort         — "timestamp" (default) | "importance"   (GET only)
                 Importance order is the review queue per
                 docs/specs/r1-review-queue.md (#144): severity DESC,
                 timestamp DESC. Combine with unread_only=1 for the
                 triage view — operator scans most-important
                 unread items first.
"""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import csrf_protect, login_required

log = logging.getLogger("monitor.api.alerts")

alerts_bp = Blueprint("alerts", __name__)


def _truthy(value: str | None) -> bool:
    if not value:
        return False
    return value.lower() in {"1", "true", "yes", "on"}


def _current_user_and_role() -> tuple[str, str]:
    """Return (username, role) for the active session.

    Falls back to ("", "viewer") on a missing session — the
    surrounding ``login_required`` decorator is the binding gate, but
    we still want sane values rather than raising downstream.
    """
    username = session.get("username", "") or ""
    role = session.get("role", "viewer") or "viewer"
    return username, role


@alerts_bp.route("/", methods=["GET"])
@alerts_bp.route("", methods=["GET"])
@login_required
def list_alerts():
    """List alerts visible to the current session, newest-first.

    See module docstring for the supported query params. The
    ``unread_count`` field is the count BEFORE pagination — the
    nav badge should consume that, not ``len(alerts)``.
    """
    user, role = _current_user_and_role()

    source = (request.args.get("source") or "").strip() or None
    severity = (request.args.get("severity") or "").strip() or None
    unread_only = _truthy(request.args.get("unread_only"))
    before = (request.args.get("before") or "").strip() or None
    sort = (request.args.get("sort") or "timestamp").strip()
    # Defensive: only accept the documented values. An unknown sort
    # token falls through to the default rather than 400-erroring —
    # query-string typos shouldn't surface as broken pages.
    if sort not in ("timestamp", "importance"):
        sort = "timestamp"

    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))

    svc = current_app.alert_center
    alerts = svc.list_alerts(
        user=user,
        role=role,
        source=source,
        severity=severity,
        unread_only=unread_only,
        limit=limit,
        before=before,
        sort=sort,
    )
    unread_count = svc.unread_count(user=user, role=role)

    return jsonify(
        {
            "alerts": alerts,
            "count": len(alerts),
            "unread_count": unread_count,
        }
    )


@alerts_bp.route("/unread-count", methods=["GET"])
@login_required
def unread_count():
    """Cheap count for the nav badge. Polled every ~30 s by the UI."""
    user, role = _current_user_and_role()
    count = current_app.alert_center.unread_count(user=user, role=role)
    return jsonify({"count": count})


@alerts_bp.route("/<alert_id>/read", methods=["POST"])
@login_required
@csrf_protect
def mark_read(alert_id: str):
    """Mark a single alert as read for the current user. Idempotent.

    Returns 400 only on a clearly bogus alert_id shape (no recognised
    typed prefix). Re-marking a read alert is a no-op success.
    """
    user, _ = _current_user_and_role()
    if not user:
        return jsonify({"ok": False, "error": "no session user"}), 401
    ok = current_app.alert_center.mark_read(user=user, alert_id=alert_id)
    if not ok:
        return jsonify({"ok": False, "error": "invalid alert id"}), 400
    return jsonify({"ok": True})


@alerts_bp.route("/mark-all-read", methods=["POST"])
@login_required
@csrf_protect
def mark_all_read():
    """Mark every alert matching the same filter set as GET as read.

    Filters can come from the JSON body OR the query string — the
    body wins on conflict. Same shape as GET so a UI that holds the
    current filter state can re-use it for the bulk action.
    """
    user, role = _current_user_and_role()
    if not user:
        return jsonify({"marked": 0, "error": "no session user"}), 401

    body = request.get_json(silent=True) or {}
    source = (body.get("source") or request.args.get("source") or "").strip() or None
    severity = (
        body.get("severity") or request.args.get("severity") or ""
    ).strip() or None
    before = (body.get("before") or request.args.get("before") or "").strip() or None

    marked = current_app.alert_center.mark_all_read(
        user=user,
        role=role,
        source=source,
        severity=severity,
        before=before,
    )
    return jsonify({"marked": marked})
