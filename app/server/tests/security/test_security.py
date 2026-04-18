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

import pytest

from monitor.auth import _login_attempts, hash_password
from monitor.models import Camera, User


def _login(app, client, username="admin", password="pass", role="admin"):
    """Helper: create user and login (kept for tests needing custom username/password)."""
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

_TRAVERSAL_PATHS = [
    # (url_suffix, needs_camera, expected_codes)
    ("/../../../etc/passwd", False, (404,)),
    ("/cam-001/../../etc/passwd/evil.mp4", True, (400, 404)),
    ("/cam-001/2026-04-09/../../etc/passwd.mp4", True, (400, 404)),
    ("/cam-001/2026-04-09/evil%00.mp4", True, (400, 404)),
    ("/cam-001/..%2F..%2Fetc%2Fpasswd/evil.mp4", True, (400, 404)),
    ("/cam-001/2026-04-09/%2e%2e%2fetc%2fpasswd.mp4", True, (400, 404)),
    ("/cam-001/2026-04-09/....//....//etc/passwd.mp4", True, (400, 404)),
]


class TestPathTraversal:
    """Verify path traversal attacks are blocked on recordings endpoints.

    Parametrized so adding a new attack vector is a one-liner.
    """

    @pytest.mark.parametrize("suffix,needs_camera,expected_codes", _TRAVERSAL_PATHS)
    def test_get_traversal_blocked(self, suffix, needs_camera, expected_codes, app, client):
        _login(app, client)
        if needs_camera:
            _add_camera(app)
        response = client.get(f"/api/v1/recordings{suffix}")
        assert response.status_code in expected_codes, (
            f"GET /api/v1/recordings{suffix} returned {response.status_code}, "
            f"expected one of {expected_codes}"
        )

    def test_traversal_in_delete_does_not_remove_arbitrary_file(self, app, client):
        """Path traversal in DELETE must not delete files outside recordings dir."""
        _login(app, client)
        safe_file = os.path.join(app.config["DATA_DIR"], "safe.txt")
        with open(safe_file, "w") as f:
            f.write("do not delete")

        client.delete("/api/v1/recordings/cam-001/../safe.txt")
        assert os.path.exists(safe_file)


# ===========================================================================
# CSRF enforcement
# ===========================================================================

# Endpoints decorated with both @admin_required and @csrf_protect.
# Each tuple: (method, url, minimal_valid_body)
_CSRF_PROTECTED_ENDPOINTS = [
    ("PUT", "/api/v1/settings", {"hostname": "test-host"}),
    ("POST", "/api/v1/settings/time", {"time": "2026-01-01T00:00:00Z"}),
    ("POST", "/api/v1/settings/wifi", {"ssid": "MyNet", "password": "pass1234"}),
    ("POST", "/api/v1/users", {"username": "newuser1", "password": "password1234"}),
    ("DELETE", "/api/v1/users/user-nobody", None),
    ("PUT", "/api/v1/users/user-nobody/password", {"new_password": "newpass1234"}),
]


class TestCSRFEnforcement:
    """Verify CSRF protection actually blocks invalid tokens.

    Every state-changing endpoint decorated with @csrf_protect must
    return 403 when the token is absent, wrong, or replayed.
    """

    def test_logout_works_without_csrf(self, app, client):
        """Logout should work — it clears the session (no additive state change)."""
        _login(app, client)
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 200

    @pytest.mark.parametrize("method,url,body", _CSRF_PROTECTED_ENDPOINTS)
    def test_missing_csrf_token_returns_403(self, method, url, body, app, client):
        """CSRF-protected endpoint must return 403 when X-CSRF-Token header is absent."""
        _login(app, client)
        # Remove the CSRF token that _login set
        client.environ_base.pop("HTTP_X_CSRF_TOKEN", None)
        resp = getattr(client, method.lower())(url, json=body)
        assert resp.status_code == 403, (
            f"{method} {url} accepted request with no CSRF token (got {resp.status_code})"
        )

    @pytest.mark.parametrize("method,url,body", _CSRF_PROTECTED_ENDPOINTS)
    def test_wrong_csrf_token_returns_403(self, method, url, body, app, client):
        """CSRF-protected endpoint must return 403 when token is wrong."""
        _login(app, client)
        client.environ_base["HTTP_X_CSRF_TOKEN"] = "deadbeefdeadbeef"
        resp = getattr(client, method.lower())(url, json=body)
        assert resp.status_code == 403, (
            f"{method} {url} accepted wrong CSRF token (got {resp.status_code})"
        )

    @pytest.mark.parametrize("method,url,body", _CSRF_PROTECTED_ENDPOINTS)
    def test_empty_csrf_token_returns_403(self, method, url, body, app, client):
        """Empty-string CSRF token must not be accepted."""
        _login(app, client)
        client.environ_base["HTTP_X_CSRF_TOKEN"] = ""
        resp = getattr(client, method.lower())(url, json=body)
        assert resp.status_code == 403, (
            f"{method} {url} accepted empty CSRF token (got {resp.status_code})"
        )

    def test_valid_csrf_token_accepted(self, app, client):
        """Sanity: request with valid token must NOT be rejected by CSRF guard."""
        _login(app, client)
        # PUT /settings is csrf-protected; valid token (set by _login) must pass guard
        resp = client.put("/api/v1/settings", json={"hostname": "home-monitor"})
        # 200 or 400 (validation) are acceptable; 403 is not
        assert resp.status_code != 403, "Valid CSRF token was rejected"

    def test_replayed_token_from_different_session_is_rejected(self, app, client):
        """A CSRF token stolen from a different session must not work."""
        _login(app, client)
        stolen_token = client.environ_base["HTTP_X_CSRF_TOKEN"]

        # Start a fresh session (logout + re-login creates a new CSRF token)
        client.post("/api/v1/auth/logout")
        _login(app, client)
        new_token = client.environ_base["HTTP_X_CSRF_TOKEN"]
        assert stolen_token != new_token, "Two logins produced the same CSRF token"

        # Use the stolen old token — must be rejected
        client.environ_base["HTTP_X_CSRF_TOKEN"] = stolen_token
        resp = client.put("/api/v1/settings", json={"hostname": "hacked"})
        assert resp.status_code == 403


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

_CAMERA_NAME_PAYLOADS = [
    ("<script>alert('xss')</script>", "XSS payload"),
    ("A" * 10000, "10k-char string"),
    ("\x00evil", "null byte"),
    ("../../../etc/passwd", "path traversal in name"),
    ("\u202e\u0041\u0042\u0043", "Unicode RTL override"),
    ("'; DROP TABLE cameras; --", "SQL injection attempt"),
    ("{\"$where\": \"1==1\"}", "NoSQL injection attempt"),
]

_LOGIN_CRASH_CASES = [
    (b"{}", "empty JSON object"),
    (b'{"username": null, "password": null}', "null values"),
    (b'{"username": "", "password": ""}', "empty strings"),
    (b"not json at all", "non-JSON body"),
    (b"[]", "JSON array instead of object"),
    (b'{"username": ' + b"A" * 100000 + b'"}', "100k username"),
]


class TestInputInjection:
    """Verify special characters and malformed input don't cause 5xx errors."""

    @pytest.mark.parametrize("name,description", _CAMERA_NAME_PAYLOADS)
    def test_hostile_camera_name_does_not_crash(self, name, description, app, client):
        """Hostile camera name payloads must return 200 or 400, never 500."""
        _login(app, client)
        _add_camera(app)
        response = client.put("/api/v1/cameras/cam-001", json={"name": name})
        assert response.status_code in (200, 400), (
            f"Camera name payload '{description}' caused {response.status_code}"
        )

    @pytest.mark.parametrize(
        "body,description",
        _LOGIN_CRASH_CASES,
        ids=[c[1].replace(" ", "_") for c in _LOGIN_CRASH_CASES],
    )
    def test_malformed_login_body_does_not_crash(self, body, description, client):
        """Malformed login bodies must return 400/401, never 500."""
        response = client.post(
            "/api/v1/auth/login",
            data=body,
            content_type="application/json",
        )
        assert response.status_code in (400, 401), (
            f"Login body '{description}' caused {response.status_code}"
        )

    def test_extremely_nested_json_does_not_crash(self, app, client):
        """Deeply nested JSON in settings update must not cause stack overflow."""
        _login(app, client)
        nested = {"a": None}
        for _ in range(200):
            nested = {"x": nested}
        resp = client.put("/api/v1/settings", json=nested)
        assert resp.status_code in (400, 422, 200)
        assert resp.status_code != 500


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
        app = create_app(
            config={
                "TESTING": True,
                "DATA_DIR": str(tmp_path),
                "CONFIG_DIR": str(tmp_path / "config"),
                "RECORDINGS_DIR": str(tmp_path / "recordings"),
                "LIVE_DIR": str(tmp_path / "live"),
                "CERTS_DIR": str(tmp_path / "certs"),
            }
        )
        assert app.config["SESSION_COOKIE_SECURE"] is True

    def test_session_cookie_httponly(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_session_cookie_samesite(self, app):
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Strict"
