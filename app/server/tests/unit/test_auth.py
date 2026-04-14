"""Tests for the authentication module."""

import time
from datetime import UTC, datetime

import pytest

from monitor.auth import (
    _check_rate_limit,
    _get_lockout_duration,
    _is_account_locked,
    _login_attempts,
    _record_attempt,
    check_password,
    hash_password,
)
from monitor.password_policy import validate_password


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limit state before each test."""
    _login_attempts.clear()
    yield
    _login_attempts.clear()


def _create_test_user(app):
    """Helper: create an admin user in the store."""
    from monitor.models import User

    user = User(
        id="user-admin",
        username="admin",
        password_hash=hash_password("correct-password"),
        role="admin",
        created_at="2026-04-09T10:00:00Z",
    )
    app.store.save_user(user)
    return user


class TestPasswordHashing:
    """Test bcrypt password hashing."""

    def test_hash_password_returns_string(self):
        h = hash_password("testpass")
        assert isinstance(h, str)
        assert h.startswith("$2b$12$")

    def test_check_password_correct(self):
        h = hash_password("mypassword")
        assert check_password("mypassword", h) is True

    def test_check_password_wrong(self):
        h = hash_password("mypassword")
        assert check_password("wrongpassword", h) is False

    def test_check_password_invalid_hash(self):
        assert check_password("test", "not-a-hash") is False

    def test_check_password_empty(self):
        assert check_password("", "") is False

    def test_different_hashes_for_same_password(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # Different salts


class TestLogin:
    """Test POST /api/v1/auth/login."""

    def test_login_success(self, app, client):
        _create_test_user(app)
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["user"]["username"] == "admin"
        assert data["user"]["role"] == "admin"
        assert "csrf_token" in data

    def test_login_wrong_password(self, app, client):
        _create_test_user(app)
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "wrong-password",
            },
        )
        assert response.status_code == 401

    def test_login_unknown_user(self, app, client):
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": "nobody",
                "password": "test",
            },
        )
        assert response.status_code == 401

    def test_login_missing_fields(self, client):
        response = client.post("/api/v1/auth/login", json={})
        assert response.status_code == 400

    def test_login_missing_password(self, client):
        response = client.post("/api/v1/auth/login", json={"username": "admin"})
        assert response.status_code == 400

    def test_login_no_json_body(self, client):
        response = client.post("/api/v1/auth/login")
        assert response.status_code == 400


class TestLogout:
    """Test POST /api/v1/auth/logout."""

    def test_logout_clears_session(self, app, client):
        _create_test_user(app)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 200

        # Verify session is cleared
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_logout_without_session(self, client):
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 200


class TestMe:
    """Test GET /api/v1/auth/me."""

    def test_me_authenticated(self, app, client):
        _create_test_user(app)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 200
        data = response.get_json()
        assert data["user"]["username"] == "admin"
        assert data["user"]["role"] == "admin"

    def test_me_unauthenticated(self, client):
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401


class TestSessionTimeout:
    """Test session expiration."""

    def test_session_expires_after_idle_timeout(self, app, client):
        _create_test_user(app)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )

        # Simulate idle timeout by manipulating session
        with client.session_transaction() as sess:
            sess["last_active"] = time.time() - 3600  # 1 hour ago

        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_session_expires_after_absolute_timeout(self, app, client):
        _create_test_user(app)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )

        # Simulate 25-hour-old session
        with client.session_transaction() as sess:
            sess["created_at"] = time.time() - 90000

        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_active_session_updates_last_active(self, app, client):
        _create_test_user(app)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )
        before = time.time()
        client.get("/api/v1/auth/me")
        with client.session_transaction() as sess:
            assert sess["last_active"] >= before


class TestRateLimiting:
    """Test login rate limiting."""

    def test_allows_normal_attempts(self, app, client):
        _create_test_user(app)
        for _ in range(5):
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "username": "admin",
                    "password": "wrong",
                },
            )
            assert response.status_code == 401

    def test_blocks_after_too_many_attempts(self, app, client):
        _create_test_user(app)
        for _ in range(10):
            client.post(
                "/api/v1/auth/login",
                json={
                    "username": "admin",
                    "password": "wrong",
                },
            )

        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )
        assert response.status_code == 429

    def test_check_rate_limit_two_tier(self):
        """_check_rate_limit returns (allowed, warn) tuple."""
        ip = "10.0.0.99"
        # Under soft limit — allowed, no warn
        for _ in range(4):
            _record_attempt(ip)
        allowed, warn = _check_rate_limit(ip)
        assert allowed is True
        assert warn is False

        # At soft limit (5) — allowed, warn=True
        _record_attempt(ip)
        allowed, warn = _check_rate_limit(ip)
        assert allowed is True
        assert warn is True

        # Between soft and hard limit
        for _ in range(4):
            _record_attempt(ip)
        allowed, warn = _check_rate_limit(ip)
        assert allowed is True
        assert warn is True

        # At hard limit (10) — blocked
        _record_attempt(ip)
        allowed, warn = _check_rate_limit(ip)
        assert allowed is False


class TestCSRF:
    """Test CSRF token handling."""

    def test_login_returns_csrf_token(self, app, client):
        _create_test_user(app)
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )
        data = response.get_json()
        assert "csrf_token" in data
        assert len(data["csrf_token"]) > 0

    def test_me_returns_csrf_token(self, app, client):
        _create_test_user(app)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )
        response = client.get("/api/v1/auth/me")
        data = response.get_json()
        assert "csrf_token" in data


class TestDecorators:
    """Test login_required and admin_required decorators."""

    def test_login_required_blocks_anonymous(self, client):
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_login_required_allows_authenticated(self, app, client):
        _create_test_user(app)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-password",
            },
        )
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 200


class TestAccountLockout:
    """Test account lockout after repeated failures (ADR-0011)."""

    def test_lockout_duration_thresholds(self):
        assert _get_lockout_duration(0) == 0
        assert _get_lockout_duration(4) == 0
        assert _get_lockout_duration(5) == 60
        assert _get_lockout_duration(9) == 60
        assert _get_lockout_duration(10) == 300
        assert _get_lockout_duration(15) == 1800

    def test_account_not_locked_when_no_lockout(self):
        from monitor.models import User

        user = User(id="u1", username="test", password_hash="x")
        assert _is_account_locked(user) is False

    def test_account_locked_with_future_timestamp(self):
        from datetime import timedelta

        from monitor.models import User

        future = (datetime.now(UTC) + timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        user = User(id="u1", username="test", password_hash="x", locked_until=future)
        assert _is_account_locked(user) is True

    def test_account_unlocked_after_expiry(self):
        from datetime import timedelta

        from monitor.models import User

        past = (datetime.now(UTC) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        user = User(id="u1", username="test", password_hash="x", locked_until=past)
        assert _is_account_locked(user) is False

    def test_login_increments_failed_count(self, app, client):
        _create_test_user(app)
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        user = app.store.get_user_by_username("admin")
        assert user.failed_logins == 1

    def test_login_resets_failed_count_on_success(self, app, client):
        user = _create_test_user(app)
        user.failed_logins = 3
        app.store.save_user(user)

        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        user = app.store.get_user_by_username("admin")
        assert user.failed_logins == 0
        assert user.locked_until == ""

    def test_locked_account_returns_423(self, app, client):
        from datetime import timedelta

        user = _create_test_user(app)
        user.locked_until = (datetime.now(UTC) + timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        app.store.save_user(user)

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        assert response.status_code == 423

    def test_lockout_triggers_after_threshold(self, app, client):
        user = _create_test_user(app)
        user.failed_logins = 4
        app.store.save_user(user)

        # 5th failure should trigger lockout
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        user = app.store.get_user_by_username("admin")
        assert user.failed_logins == 5
        assert user.locked_until != ""


class TestMustChangePassword:
    """Test must_change_password flag."""

    def test_login_signals_password_change_required(self, app, client):
        user = _create_test_user(app)
        user.must_change_password = True
        app.store.save_user(user)

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["must_change_password"] is True

    def test_login_no_flag_when_not_required(self, app, client):
        _create_test_user(app)
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        data = response.get_json()
        assert "must_change_password" not in data


class TestPasswordPolicy:
    """Test password policy enforcement (NIST SP 800-63B)."""

    def test_valid_password(self):
        assert validate_password("a-strong-password-here") == ""

    def test_empty_password(self):
        assert validate_password("") == "Password is required"

    def test_too_short(self):
        result = validate_password("short")
        assert "at least 12" in result

    def test_too_long(self):
        result = validate_password("x" * 129)
        assert "at most 128" in result

    def test_exactly_12_chars(self):
        assert validate_password("a" * 12) == ""

    def test_spaces_allowed(self):
        assert validate_password("this has spaces in it") == ""

    def test_unicode_allowed(self):
        assert validate_password("p\u00e4ssw\u00f6rd-l\u00e4nger-than-twelve") == ""

    def test_common_password_blocked(self):
        result = validate_password("administrator")
        assert "too common" in result
