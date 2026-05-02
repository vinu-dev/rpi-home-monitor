# REQ: SWR-023; RISK: RISK-011; SEC: SC-011; TEST: TC-022
"""Unit tests for UserService — user CRUD and password management."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from monitor.services.user_service import UserService


def _make_user(**overrides):
    """Create a fake user object with sensible defaults."""
    defaults = {
        "id": "user-abc12345",
        "username": "alice",
        "password_hash": "$2b$12$fakehash",
        "role": "viewer",
        "created_at": "2026-01-15T10:00:00Z",
        "last_login": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def store():
    return MagicMock()


@pytest.fixture
def audit():
    return MagicMock()


@pytest.fixture
def svc(store, audit):
    return UserService(store, audit)


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------
class TestListUsers:
    def test_returns_empty_list_when_no_users(self, store):
        store.get_users.return_value = []
        svc = UserService(store)
        assert svc.list_users() == []

    def test_returns_user_dicts_without_password(self, svc, store):
        store.get_users.return_value = [_make_user()]
        result = svc.list_users()
        assert len(result) == 1
        assert result[0]["id"] == "user-abc12345"
        assert result[0]["username"] == "alice"
        assert result[0]["role"] == "viewer"
        assert result[0]["created_at"] == "2026-01-15T10:00:00Z"
        assert result[0]["last_login"] is None
        assert "password_hash" not in result[0]
        assert "password" not in result[0]

    def test_returns_multiple_users(self, svc, store):
        store.get_users.return_value = [
            _make_user(id="user-001", username="alice"),
            _make_user(id="user-002", username="bob", role="admin"),
        ]
        result = svc.list_users()
        assert len(result) == 2
        assert result[0]["id"] == "user-001"
        assert result[1]["id"] == "user-002"
        assert result[1]["role"] == "admin"


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------
class TestCreateUser:
    def test_empty_username_rejected(self, svc):
        user, err, status = svc.create_user("", "password12345", "viewer")
        assert user is None
        assert status == 400
        assert "required" in err.lower()

    def test_whitespace_only_username_rejected(self, svc):
        user, err, status = svc.create_user("   ", "password12345", "viewer")
        assert user is None
        assert status == 400
        assert "required" in err.lower()

    def test_username_too_short(self, svc):
        user, err, status = svc.create_user("ab", "password12345", "viewer")
        assert user is None
        assert status == 400
        assert "3-32" in err

    def test_username_too_long(self, svc):
        user, err, status = svc.create_user("a" * 33, "password12345", "viewer")
        assert user is None
        assert status == 400
        assert "3-32" in err

    def test_username_at_min_length(self, svc, store):
        store.get_user_by_username.return_value = None
        user, err, status = svc.create_user("abc", "password12345", "viewer")
        assert status == 201

    def test_username_at_max_length(self, svc, store):
        store.get_user_by_username.return_value = None
        user, err, status = svc.create_user("a" * 32, "password12345", "viewer")
        assert status == 201

    def test_password_empty_rejected(self, svc):
        user, err, status = svc.create_user("alice", "", "viewer")
        assert user is None
        assert status == 400
        assert "required" in err.lower()

    def test_password_none_rejected(self, svc):
        user, err, status = svc.create_user("alice", None, "viewer")
        assert user is None
        assert status == 400

    def test_password_too_short(self, svc):
        user, err, status = svc.create_user("alice", "short", "viewer")
        assert user is None
        assert status == 400
        assert "12 characters" in err

    def test_invalid_role_rejected(self, svc):
        user, err, status = svc.create_user("alice", "password12345", "superadmin")
        assert user is None
        assert status == 400
        assert "role" in err.lower()

    def test_duplicate_username_rejected(self, svc, store):
        store.get_user_by_username.return_value = _make_user()
        user, err, status = svc.create_user("alice", "password12345", "viewer")
        assert user is None
        assert status == 409
        assert "already exists" in err.lower()

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$hashed")
    def test_successful_creation_returns_user_dict(self, mock_hash, svc, store):
        store.get_user_by_username.return_value = None
        user, err, status = svc.create_user(
            "alice",
            "password12345",
            "viewer",
            requesting_user="admin",
            requesting_ip="10.0.0.1",
        )
        assert status == 201
        assert err == ""
        assert user is not None
        assert user["username"] == "alice"
        assert user["role"] == "viewer"
        assert user["id"].startswith("user-")
        assert "created_at" in user
        assert "password_hash" not in user
        assert "password" not in user

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$hashed")
    def test_successful_creation_saves_to_store(self, mock_hash, svc, store):
        store.get_user_by_username.return_value = None
        svc.create_user("alice", "password12345", "admin")
        store.save_user.assert_called_once()
        saved = store.save_user.call_args[0][0]
        assert saved.username == "alice"
        assert saved.role == "admin"
        assert saved.password_hash == "$2b$12$hashed"

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$hashed")
    def test_create_user_logs_audit(self, mock_hash, svc, store, audit):
        store.get_user_by_username.return_value = None
        svc.create_user(
            "alice",
            "password12345",
            "viewer",
            requesting_user="admin",
            requesting_ip="10.0.0.1",
        )
        audit.log_event.assert_called_once()
        call_kwargs = audit.log_event.call_args
        assert call_kwargs[0][0] == "USER_CREATED"
        assert "alice" in call_kwargs[1]["detail"]

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$hashed")
    def test_create_user_strips_username_whitespace(self, mock_hash, svc, store):
        store.get_user_by_username.return_value = None
        user, err, status = svc.create_user("  alice  ", "password12345", "viewer")
        assert status == 201
        assert user["username"] == "alice"

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$hashed")
    def test_create_admin_user(self, mock_hash, svc, store):
        store.get_user_by_username.return_value = None
        user, err, status = svc.create_user("bob", "password12345", "admin")
        assert status == 201
        assert user["role"] == "admin"


# ---------------------------------------------------------------------------
# delete_user
# ---------------------------------------------------------------------------
class TestDeleteUser:
    def test_cannot_delete_yourself(self, svc):
        msg, status = svc.delete_user(
            "user-001",
            requesting_user_id="user-001",
            requesting_user="alice",
            requesting_ip="10.0.0.1",
        )
        assert status == 400
        assert "own account" in msg.lower()

    def test_user_not_found(self, svc, store):
        store.delete_user.return_value = False
        msg, status = svc.delete_user(
            "user-999",
            requesting_user_id="user-001",
        )
        assert status == 404
        assert "not found" in msg.lower()

    def test_successful_delete(self, svc, store):
        store.delete_user.return_value = True
        msg, status = svc.delete_user(
            "user-002",
            requesting_user_id="user-001",
            requesting_user="admin",
            requesting_ip="10.0.0.1",
        )
        assert status == 200
        assert "deleted" in msg.lower()
        store.delete_user.assert_called_once_with("user-002")

    def test_delete_logs_audit(self, svc, store, audit):
        store.delete_user.return_value = True
        svc.delete_user(
            "user-002",
            requesting_user_id="user-001",
            requesting_user="admin",
            requesting_ip="10.0.0.1",
        )
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "USER_DELETED"

    def test_delete_not_found_does_not_audit(self, svc, store, audit):
        store.delete_user.return_value = False
        svc.delete_user("user-999", requesting_user_id="user-001")
        audit.log_event.assert_not_called()


# ---------------------------------------------------------------------------
# change_password
# ---------------------------------------------------------------------------
class TestChangePassword:
    def test_non_admin_cannot_change_other_user_password(self, svc):
        msg, status = svc.change_password(
            "user-002",
            "newpassword1",
            requesting_role="viewer",
            requesting_user_id="user-001",
        )
        assert status == 403
        assert "another user" in msg.lower()

    def test_non_admin_can_change_own_password(self, svc, store):
        store.get_user.return_value = _make_user(id="user-001")
        msg, status = svc.change_password(
            "user-001",
            "newpassword1",
            requesting_role="viewer",
            requesting_user_id="user-001",
        )
        assert status == 200

    def test_admin_can_change_any_user_password(self, svc, store):
        store.get_user.return_value = _make_user(id="user-002")
        msg, status = svc.change_password(
            "user-002",
            "newpassword1",
            requesting_role="admin",
            requesting_user_id="user-001",
        )
        assert status == 200

    def test_password_too_short_rejected(self, svc):
        msg, status = svc.change_password(
            "user-001",
            "short",
            requesting_role="admin",
            requesting_user_id="user-001",
        )
        assert status == 400
        assert "12 characters" in msg

    def test_empty_password_rejected(self, svc):
        msg, status = svc.change_password(
            "user-001",
            "",
            requesting_role="admin",
            requesting_user_id="user-001",
        )
        assert status == 400

    def test_none_password_rejected(self, svc):
        msg, status = svc.change_password(
            "user-001",
            None,
            requesting_role="admin",
            requesting_user_id="user-001",
        )
        assert status == 400

    def test_user_not_found(self, svc, store):
        store.get_user.return_value = None
        msg, status = svc.change_password(
            "user-999",
            "newpassword1",
            requesting_role="admin",
            requesting_user_id="user-001",
        )
        assert status == 404
        assert "not found" in msg.lower()

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$newhash")
    def test_password_saved_to_store(self, mock_hash, svc, store):
        user = _make_user(id="user-001")
        store.get_user.return_value = user
        svc.change_password(
            "user-001",
            "newpassword1",
            requesting_role="admin",
            requesting_user_id="user-001",
        )
        store.save_user.assert_called_once()
        saved = store.save_user.call_args[0][0]
        assert saved.password_hash == "$2b$12$newhash"

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$newhash")
    def test_change_password_logs_audit(self, mock_hash, svc, store, audit):
        store.get_user.return_value = _make_user(id="user-001")
        svc.change_password(
            "user-001",
            "newpassword1",
            requesting_role="admin",
            requesting_user_id="user-001",
            requesting_user="admin",
            requesting_ip="10.0.0.1",
        )
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "PASSWORD_CHANGED"


# ---------------------------------------------------------------------------
# Admin-reset-another-user path (issue #99 slice 1)
# ---------------------------------------------------------------------------
class TestAdminResetAnotherUser:
    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$h")
    def test_force_change_sets_must_change_flag(self, mock_hash, svc, store):
        target = _make_user(id="user-002", role="viewer")
        target.must_change_password = False
        store.get_user.return_value = target
        msg, status = svc.change_password(
            "user-002",
            "temppassword123",
            requesting_role="admin",
            requesting_user_id="user-001",
            requesting_user="admin",
            requesting_ip="10.0.0.1",
            force_change_next_login=True,
        )
        assert status == 200
        saved = store.save_user.call_args[0][0]
        assert saved.must_change_password is True
        assert saved.password_hash == "$2b$12$h"

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$h")
    def test_admin_reset_logs_specific_audit_event(self, mock_hash, svc, store, audit):
        store.get_user.return_value = _make_user(id="user-002", role="viewer")
        svc.change_password(
            "user-002",
            "temppassword123",
            requesting_role="admin",
            requesting_user_id="user-001",
            requesting_user="admin",
            requesting_ip="10.0.0.1",
            force_change_next_login=True,
        )
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "PASSWORD_RESET_BY_ADMIN"

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$h")
    def test_self_change_never_sets_must_change_flag(self, mock_hash, svc, store):
        """Defence in depth: even if a self-change somehow carries
        force_change_next_login=True, the flag stays False so the user
        can't lock themselves into an infinite must-change loop."""
        target = _make_user(id="user-001")
        target.must_change_password = False
        store.get_user.return_value = target
        svc.change_password(
            "user-001",
            "newpassword12",
            requesting_role="admin",
            requesting_user_id="user-001",  # same as target — self-change
            requesting_user="admin",
            requesting_ip="10.0.0.1",
            force_change_next_login=True,
        )
        saved = store.save_user.call_args[0][0]
        assert saved.must_change_password is False

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$h")
    def test_refuses_to_force_change_the_only_admin(self, mock_hash, svc, store):
        """Safety rail: an admin-reset against the sole admin is refused
        so that admin can't be trapped in a must-change loop."""
        sole_admin = _make_user(id="user-001", role="admin")
        store.get_user.return_value = sole_admin
        store.get_users.return_value = [sole_admin]
        msg, status = svc.change_password(
            "user-001",
            "newpassword12",
            requesting_role="admin",
            requesting_user_id="user-002",  # another admin trying to reset
            requesting_user="other-admin",
            requesting_ip="10.0.0.1",
            force_change_next_login=True,
        )
        assert status == 400
        assert "only admin" in msg.lower()
        store.save_user.assert_not_called()

    def test_safety_rail_uses_real_store_method_name(self, tmp_path):
        """Regression for issue #117 — the guard previously called
        ``self._store.list_users()``, a method that doesn't exist on the
        concrete ``Store``. Exercise the real class (not a MagicMock) so
        any future rename of get_users → list_users fails loudly here
        instead of crashing in production with AttributeError."""
        from monitor.services.user_service import UserService
        from monitor.store import Store

        real_store = Store(config_dir=str(tmp_path))
        # Seed two admins so the "only admin" guard is NOT triggered —
        # we just want the code path that calls get_users() to execute.
        from monitor.auth import hash_password
        from monitor.models import User

        for i, uname in enumerate(("alice", "bob"), start=1):
            real_store.save_user(
                User(
                    id=f"user-{i:03d}",
                    username=uname,
                    password_hash=hash_password("temp" + "x" * 12),
                    role="admin",
                    must_change_password=False,
                )
            )

        svc = UserService(real_store, audit=None)
        msg, status = svc.change_password(
            "user-001",
            "new-temp-password-123",
            requesting_role="admin",
            requesting_user_id="user-002",  # the other admin
            requesting_user="bob",
            requesting_ip="10.0.0.1",
            force_change_next_login=True,
        )
        # Must succeed — not crash with AttributeError.
        assert status == 200, msg
        reloaded = real_store.get_user("user-001")
        assert reloaded.must_change_password is True


# ---------------------------------------------------------------------------
# _log_audit (fail-silent behavior)
# ---------------------------------------------------------------------------
class TestAuditLogging:
    def test_no_audit_service_does_not_raise(self, store):
        svc = UserService(store, audit=None)
        # Should not raise even without audit
        store.delete_user.return_value = True
        msg, status = svc.delete_user(
            "user-002",
            requesting_user_id="user-001",
            requesting_user="admin",
            requesting_ip="10.0.0.1",
        )
        assert status == 200

    def test_audit_exception_does_not_break_operation(self, store, audit):
        audit.log_event.side_effect = RuntimeError("audit db down")
        svc = UserService(store, audit)
        store.delete_user.return_value = True
        msg, status = svc.delete_user(
            "user-002",
            requesting_user_id="user-001",
            requesting_user="admin",
            requesting_ip="10.0.0.1",
        )
        # Operation succeeds even though audit failed
        assert status == 200
        assert "deleted" in msg.lower()

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$hashed")
    def test_audit_failure_on_create_does_not_break(self, mock_hash, store, audit):
        audit.log_event.side_effect = RuntimeError("audit db down")
        svc = UserService(store, audit)
        store.get_user_by_username.return_value = None
        user, err, status = svc.create_user("alice", "password12345", "viewer")
        assert status == 201
        assert user is not None

    @patch("monitor.services.user_service.hash_password", return_value="$2b$12$newhash")
    def test_audit_failure_on_password_change_does_not_break(
        self,
        mock_hash,
        store,
        audit,
    ):
        audit.log_event.side_effect = RuntimeError("audit db down")
        svc = UserService(store, audit)
        store.get_user.return_value = _make_user(id="user-001")
        msg, status = svc.change_password(
            "user-001",
            "newpassword1",
            requesting_role="admin",
            requesting_user_id="user-001",
        )
        assert status == 200
