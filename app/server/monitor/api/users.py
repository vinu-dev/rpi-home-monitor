"""
User management API.

Endpoints:
  GET    /users              - list users (admin)
  POST   /users              - create user (admin)
  DELETE /users/<id>         - delete user (admin)
  PUT    /users/<id>/password - change password (admin or self)

Roles: admin (full access), viewer (read-only).
Passwords stored as bcrypt hashes (cost 12).
"""
import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, hash_password, login_required
from monitor.models import User

users_bp = Blueprint("users", __name__)

VALID_ROLES = {"admin", "viewer"}


@users_bp.route("", methods=["GET"])
@admin_required
def list_users():
    """List all users (admin only). Passwords excluded."""
    users = current_app.store.get_users()
    return jsonify([
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "created_at": u.created_at,
            "last_login": u.last_login,
        }
        for u in users
    ]), 200


@users_bp.route("", methods=["POST"])
@admin_required
def create_user():
    """Create a new user (admin only)."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "viewer")

    if not username:
        return jsonify({"error": "Username is required"}), 400
    if len(username) < 3 or len(username) > 32:
        return jsonify({"error": "Username must be 3-32 characters"}), 400
    if not password or len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if role not in VALID_ROLES:
        return jsonify({"error": f"Role must be one of: {', '.join(sorted(VALID_ROLES))}"}), 400

    # Check for duplicate username
    existing = current_app.store.get_user_by_username(username)
    if existing:
        return jsonify({"error": "Username already exists"}), 409

    user = User(
        id=f"user-{uuid.uuid4().hex[:8]}",
        username=username,
        password_hash=hash_password(password),
        role=role,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    current_app.store.save_user(user)

    audit = getattr(current_app, "audit", None)
    if audit:
        audit.log_event(
            "USER_CREATED",
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"created user '{username}' with role '{role}'",
        )

    return jsonify({
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "created_at": user.created_at,
    }), 201


@users_bp.route("/<user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    """Delete a user (admin only). Cannot delete yourself."""
    if user_id == session.get("user_id"):
        return jsonify({"error": "Cannot delete your own account"}), 400

    deleted = current_app.store.delete_user(user_id)
    if not deleted:
        return jsonify({"error": "User not found"}), 404

    audit = getattr(current_app, "audit", None)
    if audit:
        audit.log_event(
            "USER_DELETED",
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"deleted user {user_id}",
        )

    return jsonify({"message": "User deleted"}), 200


@users_bp.route("/<user_id>/password", methods=["PUT"])
@login_required
def change_password(user_id):
    """Change a user's password. Admin can change any, users can change own."""
    # Non-admin can only change their own password
    if session.get("role") != "admin" and session.get("user_id") != user_id:
        return jsonify({"error": "Cannot change another user's password"}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    new_password = data.get("new_password", "")
    if not new_password or len(new_password) < 8:
        return jsonify({"error": "New password must be at least 8 characters"}), 400

    user = current_app.store.get_user(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    user.password_hash = hash_password(new_password)
    current_app.store.save_user(user)

    audit = getattr(current_app, "audit", None)
    if audit:
        audit.log_event(
            "PASSWORD_CHANGED",
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"password changed for user {user_id}",
        )

    return jsonify({"message": "Password updated"}), 200
