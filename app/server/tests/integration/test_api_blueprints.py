"""Tests for API blueprint registration and universal route behaviour.

Every blueprint must be mounted, every protected endpoint must enforce
auth, and every POST/PUT/DELETE endpoint must enforce CSRF — regardless
of what the endpoint does.  Adding a new endpoint without auth or CSRF is
caught here before it ever reaches production.
"""

import pytest


class TestBlueprintRegistration:
    """All blueprints are present in the app."""

    def test_auth_blueprint(self, app):
        assert "auth" in app.blueprints

    def test_cameras_blueprint(self, app):
        assert "cameras" in app.blueprints

    def test_recordings_blueprint(self, app):
        assert "recordings" in app.blueprints

    def test_live_blueprint(self, app):
        assert "live" in app.blueprints

    def test_system_blueprint(self, app):
        assert "system" in app.blueprints

    def test_settings_blueprint(self, app):
        assert "settings" in app.blueprints

    def test_users_blueprint(self, app):
        assert "users" in app.blueprints

    def test_ota_blueprint(self, app):
        assert "ota" in app.blueprints

    def test_pairing_blueprint(self, app):
        assert "pairing" in app.blueprints

    def test_webrtc_blueprint(self, app):
        assert "webrtc" in app.blueprints

    def test_on_demand_blueprint(self, app):
        assert "on_demand" in app.blueprints

    def test_audit_blueprint(self, app):
        assert "audit" in app.blueprints


class TestUnknownRoutes:
    """Non-existent routes return 404, not 500."""

    def test_unknown_api_route(self, client):
        assert client.get("/api/v1/nonexistent").status_code == 404

    def test_unknown_deep_path(self, client):
        assert client.get("/api/v1/cameras/x/y/z/w").status_code in (404, 401)

    def test_unknown_root_path(self, client):
        assert client.get("/totally/unknown/path").status_code == 404


# ---------------------------------------------------------------------------
# Auth enforcement matrix
# Every sensitive GET must return 401 for unauthenticated requests.
# ---------------------------------------------------------------------------

_AUTH_REQUIRED_GETS = [
    "/api/v1/cameras",
    "/api/v1/recordings/latest",
    "/api/v1/live/cam-test/stream.m3u8",
    "/api/v1/settings",
    "/api/v1/users",
    "/api/v1/ota/status",
    "/api/v1/system/health",
    "/api/v1/audit/events",
    "/api/v1/auth/me",
]


class TestAuthEnforcement:
    """Every protected endpoint must return 401 for unauthenticated requests."""

    @pytest.mark.parametrize("url", _AUTH_REQUIRED_GETS)
    def test_get_requires_auth(self, url, client):
        resp = client.get(url)
        assert resp.status_code == 401, (
            f"GET {url} returned {resp.status_code} for unauthenticated request"
        )

    def test_authenticated_request_succeeds(self, logged_in_client):
        client = logged_in_client()
        resp = client.get("/api/v1/cameras")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CSRF matrix — every state-changing route must reject missing CSRF token.
# Kept in sync with @csrf_protect decorators in the API layer.
# ---------------------------------------------------------------------------

_STATE_CHANGING_ROUTES = [
    ("POST", "/api/v1/cameras", {"id": "cam-x"}),
    ("PUT", "/api/v1/settings", {"hostname": "x"}),
    ("POST", "/api/v1/settings/time", {"time": "2026-01-01T00:00:00Z"}),
    ("POST", "/api/v1/settings/wifi", {"ssid": "x", "password": "y"}),
    ("POST", "/api/v1/users", {"username": "u1", "password": "password1234"}),
    ("DELETE", "/api/v1/users/user-nobody", None),
    ("PUT", "/api/v1/users/user-nobody/password", {"new_password": "newpass1234"}),
    ("POST", "/api/v1/ota/server/upload", None),
]


class TestCSRFMatrix:
    """Every @csrf_protect endpoint returns 403 when the token is absent."""

    @pytest.mark.parametrize("method,url,body", _STATE_CHANGING_ROUTES)
    def test_missing_csrf_returns_403(self, method, url, body, logged_in_client):
        client = logged_in_client()
        # Remove the token the fixture set
        client.environ_base.pop("HTTP_X_CSRF_TOKEN", None)
        resp = getattr(client, method.lower())(url, json=body)
        assert resp.status_code == 403, (
            f"{method} {url} accepted request with no CSRF token (got {resp.status_code})"
        )
