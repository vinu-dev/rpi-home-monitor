# REQ: SWR-023; RISK: RISK-011; SEC: SC-011; TEST: TC-022
"""
User management API — thin HTTP adapter.

All business logic delegated to UserService.

Endpoints:
  GET    /users              - list users (admin)
  POST   /users              - create user (admin)
  DELETE /users/<id>         - delete user (admin)
  PUT    /users/<id>/password - change password (admin or self)
"""

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect, login_required

users_bp = Blueprint("users", __name__)


@users_bp.route("", methods=["GET"])
@admin_required
def list_users():
    """List all users (admin only). Passwords excluded."""
    users = current_app.user_service.list_users()
    return jsonify(users), 200


@users_bp.route("", methods=["POST"])
@admin_required
@csrf_protect
def create_user():
    """Create a new user (admin only)."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    result, err, status = current_app.user_service.create_user(
        username=data.get("username", ""),
        password=data.get("password", ""),
        role=data.get("role", "viewer"),
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if err:
        return jsonify({"error": err}), status
    return jsonify(result), status


@users_bp.route("/<user_id>", methods=["DELETE"])
@admin_required
@csrf_protect
def delete_user(user_id):
    """Delete a user (admin only). Cannot delete yourself."""
    msg, status = current_app.user_service.delete_user(
        user_id=user_id,
        requesting_user_id=session.get("user_id", ""),
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if status != 200:
        return jsonify({"error": msg}), status
    return jsonify({"message": msg}), status


@users_bp.route("/<user_id>/password", methods=["PUT"])
@login_required
@csrf_protect
def change_password(user_id):
    """Change a user's password. Admin can change any, users can change own."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    # ``force_change`` is the admin-reset handshake (issue #99 slice 1):
    # when true + admin + target != self, the target's must_change_password
    # flag stays set so they have to rotate on next login. Accepted but
    # silently ignored on self-change — enforced in user_service.
    msg, status = current_app.user_service.change_password(
        user_id=user_id,
        new_password=data.get("new_password", ""),
        requesting_role=session.get("role", ""),
        requesting_user_id=session.get("user_id", ""),
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
        force_change_next_login=bool(data.get("force_change", False)),
    )
    if status != 200:
        return jsonify({"error": msg}), status
    # If the caller just cleared their OWN forced-change flag, drop the
    # session-level gate so subsequent requests go straight through. A
    # stale "True" here would keep the user locked on the change screen
    # even though the DB flag has been cleared.
    if user_id == session.get("user_id"):
        session["must_change_password"] = False
    return jsonify({"message": msg}), status
