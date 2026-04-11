"""
User management service — handles user CRUD and password changes.

Single responsibility: all user business logic lives here.
Routes in api/users.py are thin HTTP adapters that delegate here.

Design:
- Constructor injection (store, audit)
- Fail-silent audit (audit errors never break operations)
- Returns (result, error, status_code) tuples for routes to unpack
"""

import logging
import uuid
from datetime import UTC, datetime

from monitor.auth import hash_password
from monitor.models import User

log = logging.getLogger("monitor.services.user_service")

VALID_ROLES = {"admin", "viewer"}


class UserService:
    """Manages user CRUD operations and password changes."""

    def __init__(self, store, audit=None):
        self._store = store
        self._audit = audit

    def list_users(self) -> list[dict]:
        """List all users. Passwords excluded from output."""
        users = self._store.get_users()
        return [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "created_at": u.created_at,
                "last_login": u.last_login,
            }
            for u in users
        ]

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "viewer",
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[dict | None, str, int]:
        """Create a new user.

        Returns (user_dict, error_message, status_code).
        """
        # Validate input
        username = username.strip()
        if not username:
            return None, "Username is required", 400
        if len(username) < 3 or len(username) > 32:
            return None, "Username must be 3-32 characters", 400
        if not password or len(password) < 8:
            return None, "Password must be at least 8 characters", 400
        if role not in VALID_ROLES:
            return None, f"Role must be one of: {', '.join(sorted(VALID_ROLES))}", 400

        # Check for duplicate
        if self._store.get_user_by_username(username):
            return None, "Username already exists", 409

        user = User(
            id=f"user-{uuid.uuid4().hex[:8]}",
            username=username,
            password_hash=hash_password(password),
            role=role,
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self._store.save_user(user)

        self._log_audit(
            "USER_CREATED",
            requesting_user,
            requesting_ip,
            f"created user '{username}' with role '{role}'",
        )

        return (
            {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "created_at": user.created_at,
            },
            "",
            201,
        )

    def delete_user(
        self,
        user_id: str,
        requesting_user_id: str = "",
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        """Delete a user. Cannot delete yourself.

        Returns (message, status_code).
        """
        if user_id == requesting_user_id:
            return "Cannot delete your own account", 400

        deleted = self._store.delete_user(user_id)
        if not deleted:
            return "User not found", 404

        self._log_audit(
            "USER_DELETED",
            requesting_user,
            requesting_ip,
            f"deleted user {user_id}",
        )

        return "User deleted", 200

    def change_password(
        self,
        user_id: str,
        new_password: str,
        requesting_role: str = "",
        requesting_user_id: str = "",
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        """Change a user's password. Admin can change any, users change own.

        Returns (message, status_code).
        """
        # Authorization check
        if requesting_role != "admin" and requesting_user_id != user_id:
            return "Cannot change another user's password", 403

        if not new_password or len(new_password) < 8:
            return "New password must be at least 8 characters", 400

        user = self._store.get_user(user_id)
        if not user:
            return "User not found", 404

        user.password_hash = hash_password(new_password)
        self._store.save_user(user)

        self._log_audit(
            "PASSWORD_CHANGED",
            requesting_user,
            requesting_ip,
            f"password changed for user {user_id}",
        )

        return "Password updated", 200

    def _log_audit(self, event: str, user: str, ip: str, detail: str):
        """Log an audit event. Never raises."""
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception:
            log.debug("Audit log failed for %s (non-fatal)", event)
