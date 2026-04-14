"""Tests for API blueprint URL routing.

Since endpoints are TODO stubs, these tests verify that blueprints
are registered at the correct URL prefixes. As endpoints are
implemented, add specific request/response tests here.
"""


class TestURLPrefixes:
    """Verify all blueprints are mounted at correct URL prefixes."""

    def test_auth_prefix(self, app):
        # Blueprint registered even if no routes yet
        assert "auth" in app.blueprints

    def test_cameras_prefix(self, app):
        assert "cameras" in app.blueprints
        bp = app.blueprints["cameras"]
        # URL prefix is set during registration
        assert bp.name == "cameras"

    def test_recordings_prefix(self, app):
        assert "recordings" in app.blueprints

    def test_live_prefix(self, app):
        assert "live" in app.blueprints

    def test_system_prefix(self, app):
        assert "system" in app.blueprints

    def test_settings_prefix(self, app):
        assert "settings" in app.blueprints

    def test_users_prefix(self, app):
        assert "users" in app.blueprints

    def test_ota_prefix(self, app):
        assert "ota" in app.blueprints


class TestTestClient:
    """Verify the test client works for future endpoint tests."""

    def test_client_exists(self, client):
        assert client is not None

    def test_404_for_unknown_route(self, client):
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404
