"""Tests for the authentication module."""

import time

import pytest

from monitor.auth import _login_attempts, check_password, hash_password


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
