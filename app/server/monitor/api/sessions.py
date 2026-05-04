# REQ: SWR-001, SWR-002; RISK: RISK-002, RISK-020; SEC: SC-001, SC-020; TEST: TC-004, TC-011
"""Active-session API surface for enumeration and revocation."""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import csrf_protect, login_required
from monitor.services.session_service import LEGACY_SESSION_ID

sessions_bp = Blueprint("sessions", __name__)


def _current_session_id() -> str:
    sid = session.get("sid")
    return sid if isinstance(sid, str) else ""


def _sort_rows(rows: list[dict]) -> list[dict]:
    def key(row: dict) -> tuple[int, float, str]:
        last_active = row.get("last_active") or ""
        try:
            stamp = datetime.fromisoformat(
                last_active.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            stamp = 0.0
        return (0 if row.get("is_current") else 1, -stamp, row.get("username") or "")

    return sorted(rows, key=key)


@sessions_bp.route("", methods=["GET"])
@login_required
def list_sessions():
    """List the caller's sessions, or every session for admins."""
    include_all = session.get("role") == "admin" and request.args.get("scope") == "all"
    service = current_app.session_service
    rows = service.list_sessions(
        requesting_user_id=session.get("user_id", ""),
        current_session_id=_current_session_id(),
        include_all=include_all,
    )

    if not _current_session_id():
        rows.append(
            service.legacy_row(
                dict(session),
                source_ip=request.remote_addr or "",
                user_agent=request.headers.get("User-Agent", ""),
            )
        )
    rows = _sort_rows(rows)
    return jsonify({"sessions": rows, "scope": "all" if include_all else "self"}), 200


@sessions_bp.route("/others", methods=["DELETE"])
@login_required
@csrf_protect
def revoke_other_sessions():
    """Revoke every other server-side session owned by the caller."""
    revoked_count = current_app.session_service.revoke_others(
        session.get("user_id", ""),
        except_session_id=_current_session_id(),
        actor_user=session.get("username", ""),
        actor_ip=request.remote_addr or "",
    )
    return jsonify({"revoked_count": revoked_count}), 200


@sessions_bp.route("/<session_id>", methods=["DELETE"])
@login_required
@csrf_protect
def revoke_session(session_id: str):
    """Revoke one session by opaque id, subject to ownership/admin rules."""
    actor_user = session.get("username", "")
    actor_role = session.get("role", "")
    actor_ip = request.remote_addr or ""
    current_sid = _current_session_id()

    if session_id == LEGACY_SESSION_ID:
        if current_sid or "user_id" not in session:
            return jsonify({"error": "Session not found"}), 404
        audit = getattr(current_app, "audit", None)
        if audit:
            audit.log_event(
                "SESSION_REVOKED",
                user=actor_user,
                ip=actor_ip,
                detail=(
                    f"target_user={session.get('username', '')} "
                    f"target_session=legacy-current source_ip={actor_ip}"
                ),
            )
        session.clear()
        return jsonify({"revoked": True}), 200

    target = current_app.session_service.get(session_id)
    if target is None:
        return jsonify({"error": "Session not found"}), 404
    if actor_role != "admin" and target.user_id != session.get("user_id", ""):
        return jsonify({"error": "Session not found"}), 404

    revoked = current_app.session_service.revoke(
        session_id,
        actor_user=actor_user,
        actor_role=actor_role,
        actor_ip=actor_ip,
    )
    if revoked is None:
        return jsonify({"error": "Session not found"}), 404
    if session_id == current_sid:
        session.clear()
    return jsonify({"revoked": True}), 200
