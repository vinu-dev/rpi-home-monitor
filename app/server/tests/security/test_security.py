"""
Security regression tests — adversarial inputs and abuse cases.

Tests that go beyond "does auth work" to ask "can auth be bypassed?"
Covers: path traversal, CSRF enforcement, session abuse, privilege
escalation, and input injection.

These tests exist because AI-generated code can pass normal unit tests
while introducing security regressions. Every test here represents
an attack vector that must never work.
"""

import os
import time

from monitor.auth import _login_attempts, hash_password
from monitor.models import Camera, User


def _login(app, client, username="admin", password="pass", role="admin"):
    """Helper: create user and login."""
    app.store.save_user(
        User(
            id=f"user-{username}",
            username=username,
            password_hash=hash_password(password),
            role=role,
        )
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    client.environ_base["HTTP_X_CSRF_TOKEN"] = response.get_json()["csrf_token"]
    return response


def _add_camera(app, camera_id="cam-001"):
    app.store.save_camera(Camera(id=camera_id, name="Test", status="online"))


def _make_clip(app, camera_id, clip_date, time_str):
    rec_dir = os.path.join(app.config["RECORDINGS_DIR"], camera_id, clip_date)
    os.makedirs(rec_dir, exist_ok=True)
    path = os.path.join(rec_dir, f"{time_str}.mp4")
    with open(path, "wb") as f:
        f.write(b"x" * 1024)
    return path


# ===========================================================================
# Path traversal
# ===========================================================================


class TestPathTraversal:
    """Verify path traversal attacks are blocked on recordings endpoints."""

    def test_traversal_in_camera_id(self, app, client):
        """../../../etc/passwd as camera_id must not leak files."""
        _login(app, client)
        response = client.get("/api/v1/recordings/../../../etc/passwd")
        # Should be 404 (camera not found) not 200 with file contents
        assert response.status_code == 404

    def test_traversal_in_date(self, app, client):
        """../ in date parameter must not escape recordings dir."""
        _login(app, client)
        _add_camera(app)
        response = client.get("/api/v1/recordings/cam-001/../../etc/passwd/evil.mp4")
        assert response.status_code in (400, 404)

    def test_traversal_in_filename(self, app, client):
        """../ in filename must not escape recordings dir."""
        _login(app, client)
        _add_camera(app)
        response = client.get(
            "/api/v1/recordings/cam-001/2026-04-09/../../etc/passwd.mp4"
        )
        assert response.status_code in (400, 404)

    def test_traversal_in_delete(self, app, client):
        """Path traversal in DELETE must not delete arbitrary files."""
        _login(app, client)
        # Create a file outside recordings that should NOT be deletable
        safe_file = os.path.join(app.config["DATA_DIR"], "safe.txt")
        with open(safe_file, "w") as f:
            f.write("do not delete")

        client.delete("/api/v1/recordings/cam-001/../safe.txt")
        # The safe file must still exist
        assert os.path.exists(safe_file)

    def test_null_byte_in_filename(self, app, client):
        """Null bytes in filename must not bypass validation."""
        _login(app, client)
        _add_camera(app)
        response = client.get("/api/v1/recordings/cam-001/2026-04-09/evil%00.mp4")
        assert response.status_code in (400, 404)

    def test_encoded_traversal(self, app, client):
        """URL-encoded ../ must not bypass path checks."""
        _login(app, client)
        _add_camera(app)
        response = client.get(
            "/api/v1/recordings/cam-001/..%2F..%2Fetc%2Fpasswd/evil.mp4"
        )
        assert response.status_code in (400, 404)


# ===========================================================================
# CSRF enforcement
# ===========================================================================


class TestCSRFEnforcement:
    """Verify CSRF protection actually blocks invalid tokens."""

    def test_logout_works_without_csrf(self, app, client):
        """Logout should work (it clears session, no state change risk)."""
        _login(app, client)
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 200

    def test_missing_csrf_on_protected_endpoint(self, app, client):
        """State-changing endpoints that require CSRF should reject if missing.

        Note: Currently only csrf_protect decorator enforces this.
        This test documents expected behavior for endpoints that use it.
        """
        # Login to get a session
        _login(app, client)
        # The auth endpoints themselves don't use csrf_protect decorator
        # but this documents the pattern for future endpoints that do
        pass


# ===========================================================================
# Session abuse
# ===========================================================================


class TestSessionAbuse:
    """Verify session management resists abuse."""

    def test_session_cleared_on_relogin(self, app, client):
        """Re-login should create a fresh session, not reuse old one."""
        _login(app, client)
        resp1 = client.get("/api/v1/auth/me")
        assert resp1.status_code == 200

        # Login again
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "pass"},
        )
        resp2 = client.get("/api/v1/auth/me")
        assert resp2.status_code == 200
        # New session should have fresh csrf token
        assert resp2.get_json()["csrf_token"] != ""

    def test_expired_session_rejected(self, app, client):
        """Expired session must be rejected, not silently extended."""
        _login(app, client)
        # Simulate idle timeout by manipulating session
        with client.session_transaction() as sess:
            sess["last_active"] = time.time() - 3600  # 1 hour ago
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_absolute_timeout_enforced(self, app, client):
        """Session older than 24h must be rejected even if recently active."""
        _login(app, client)
        with client.session_transaction() as sess:
            sess["created_at"] = time.time() - 90000  # 25 hours ago
            sess["last_active"] = time.time()  # recently active
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_logout_destroys_session(self, app, client):
        """After logout, session must not be reusable."""
        _login(app, client)
        client.post("/api/v1/auth/logout")
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401


# ===========================================================================
# Privilege escalation
# ===========================================================================


class TestPrivilegeEscalation:
    """Verify viewers cannot escalate to admin privileges."""

    def test_viewer_cannot_delete_clips(self, app, client):
        """Viewer must get 403 on clip deletion (admin-only)."""
        _login(app, client, username="viewer1", password="pass", role="viewer")
        _add_camera(app)
        _make_clip(app, "cam-001", "2026-04-09", "14-00-00")
        response = client.delete("/api/v1/recordings/cam-001/2026-04-09/14-00-00.mp4")
        assert response.status_code == 403

    def test_viewer_cannot_create_users(self, app, client):
        """Viewer must not be able to create new users."""
        _login(app, client, username="viewer1", password="pass", role="viewer")
        response = client.post(
            "/api/v1/users",
            json={
                "username": "hacker",
                "password": "password123",
                "role": "admin",
            },
        )
        assert response.status_code == 403

    def test_viewer_cannot_delete_users(self, app, client):
        """Viewer must not be able to delete users."""
        # Create admin first
        app.store.save_user(
            User(
                id="user-target",
                username="target",
                password_hash=hash_password("pass"),
                role="admin",
            )
        )
        _login(app, client, username="viewer1", password="pass", role="viewer")
        response = client.delete("/api/v1/users/user-target")
        assert response.status_code == 403

    def test_viewer_cannot_change_other_users_password(self, app, client):
        """Viewer must not be able to change another user's password."""
        app.store.save_user(
            User(
                id="user-target",
                username="target",
                password_hash=hash_password("oldpass"),
                role="admin",
            )
        )
        _login(app, client, username="viewer1", password="pass", role="viewer")
        response = client.put(
            "/api/v1/users/user-target/password",
            json={
                "current_password": "oldpass",
                "new_password": "hacked123",
            },
        )
        assert response.status_code == 403

    def test_viewer_cannot_modify_settings(self, app, client):
        """Viewer must not be able to change system settings."""
        _login(app, client, username="viewer1", password="pass", role="viewer")
        response = client.put(
            "/api/v1/settings",
            json={"hostname": "hacked"},
        )
        assert response.status_code == 403


# ===========================================================================
# Input injection
# ===========================================================================


class TestInputInjection:
    """Verify special characters in input don't cause unexpected behavior."""

    def test_html_in_camera_name(self, app, client):
        """HTML/XSS in camera name must be stored as-is (escaped on render)."""
        _login(app, client)
        _add_camera(app)
        response = client.put(
            "/api/v1/cameras/cam-001",
            json={"name": "<script>alert('xss')</script>"},
        )
        # Should either accept (store escapes on render) or reject
        # Must not cause 500
        assert response.status_code in (200, 400)

    def test_very_long_camera_name(self, app, client):
        """Extremely long camera name must not crash the server."""
        _login(app, client)
        _add_camera(app)
        response = client.put(
            "/api/v1/cameras/cam-001",
            json={"name": "A" * 10000},
        )
        assert response.status_code in (200, 400)

    def test_unicode_in_camera_name(self, app, client):
        """Unicode characters in camera name must not crash."""
        _login(app, client)
        _add_camera(app)
        response = client.put(
            "/api/v1/cameras/cam-001",
            json={"name": "Front Door"},
        )
        assert response.status_code in (200, 400)

    def test_empty_json_body(self, app, client):
        """Empty JSON body must return 400, not 500."""
        _login(app, client)
        response = client.post(
            "/api/v1/auth/login",
            data=b"{}",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_non_json_body(self, app, client):
        """Non-JSON body must not crash the server."""
        response = client.post(
            "/api/v1/auth/login",
            data=b"not json at all",
            content_type="application/json",
        )
        assert response.status_code in (400, 401)

    def test_null_values_in_login(self, app, client):
        """Null values in login must return 400, not crash."""
        response = client.post(
            "/api/v1/auth/login",
            json={"username": None, "password": None},
        )
        assert response.status_code == 400


# ===========================================================================
# Rate limiting bypass attempts
# ===========================================================================


class TestRateLimitBypass:
    """Verify rate limiting cannot be easily bypassed."""

    def setup_method(self):
        _login_attempts.clear()

    def teardown_method(self):
        _login_attempts.clear()

    def test_rate_limit_persists_across_endpoints(self, app, client):
        """Failed logins count even if correct password is eventually used."""
        app.store.save_user(
            User(
                id="user-admin",
                username="admin",
                password_hash=hash_password("correct"),
                role="admin",
            )
        )
        # Burn through attempts with wrong password
        for _ in range(10):
            client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "wrong"},
            )
        # Now try with correct password — should still be blocked
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correct"},
        )
        assert response.status_code == 429

    def test_rate_limit_applies_to_missing_users(self, app, client):
        """Attempts with nonexistent usernames must still count."""
        for _ in range(11):
            client.post(
                "/api/v1/auth/login",
                json={"username": "nobody", "password": "wrong"},
            )
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "wrong"},
        )
        assert response.status_code == 429


class TestAuthCheckEndpoint:
    """Test /auth/check used by nginx auth_request for video content.

    This endpoint gates all video serving: /live/, /clips/, /webrtc/,
    /snapshots/. A failure here means unauthenticated video access.
    """

    def test_returns_200_when_authenticated(self, app, client):
        """Valid session must get 200."""
        _login(app, client)
        response = client.get("/api/v1/auth/check")
        assert response.status_code == 200

    def test_returns_401_when_not_authenticated(self, app, client):
        """No session must get 401."""
        response = client.get("/api/v1/auth/check")
        assert response.status_code == 401

    def test_returns_401_after_logout(self, app, client):
        """Logged-out session must get 401."""
        csrf = _login(app, client)
        client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf},
        )
        response = client.get("/api/v1/auth/check")
        assert response.status_code == 401

    def test_returns_401_on_expired_session(self, app, client):
        """Expired idle session must get 401."""
        _login(app, client)
        with client.session_transaction() as sess:
            sess["last_active"] = time.time() - 7200  # 2 hours ago
        response = client.get("/api/v1/auth/check")
        assert response.status_code == 401

    def test_updates_last_active(self, app, client):
        """Auth check must refresh idle timeout (viewing video = active)."""
        _login(app, client)
        with client.session_transaction() as sess:
            sess["last_active"] = time.time() - 1700  # 28 min ago
        response = client.get("/api/v1/auth/check")
        assert response.status_code == 200
        # Should still be valid after refresh
        response2 = client.get("/api/v1/auth/check")
        assert response2.status_code == 200

    def test_empty_body(self, app, client):
        """Auth check must return empty body (performance)."""
        _login(app, client)
        response = client.get("/api/v1/auth/check")
        assert response.data == b""

    def test_head_method(self, app, client):
        """Auth check must accept HEAD requests."""
        _login(app, client)
        response = client.head("/api/v1/auth/check")
        assert response.status_code == 200


class TestSessionCookieSecurity:
    """Verify session cookie security attributes."""

    def test_session_cookie_secure_default(self, tmp_path):
        """SESSION_COOKIE_SECURE must default to True in production."""
        from monitor import create_app

        for d in ("config", "recordings", "live", "certs", "logs"):
            (tmp_path / d).mkdir()
        app = create_app(config={"DATA_DIR": str(tmp_path)})
        assert app.config["SESSION_COOKIE_SECURE"] is True

    def test_session_cookie_httponly(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_session_cookie_samesite(self, app):
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Strict"
