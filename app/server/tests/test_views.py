"""
Tests for view routes — HTML page serving and redirects.
"""
import os
import pytest


class TestIndex:
    """Tests for GET /."""

    def test_redirects_to_setup_when_not_configured(self, client):
        response = client.get("/")
        assert response.status_code == 302
        assert "/setup" in response.headers["Location"]

    def test_redirects_to_login_when_setup_done(self, app, client):
        # Mark setup complete
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.get("/")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_redirects_to_dashboard_when_authenticated(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/")
        assert response.status_code == 302
        assert "/dashboard" in response.headers["Location"]


class TestSetupPage:
    """Tests for GET /setup."""

    def test_shows_setup_wizard(self, client):
        response = client.get("/setup")
        assert response.status_code == 200
        assert b"Home Monitor" in response.data

    def test_redirects_to_login_if_setup_done(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.get("/setup")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


class TestLoginPage:
    """Tests for GET /login."""

    def test_redirects_to_setup_if_not_configured(self, client):
        response = client.get("/login")
        assert response.status_code == 302
        assert "/setup" in response.headers["Location"]

    def test_shows_login_page(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.get("/login")
        assert response.status_code == 200
        assert b"Sign In" in response.data or b"login" in response.data.lower()


class TestProtectedPages:
    """Tests for dashboard, live, recordings, settings — all require auth."""

    @pytest.fixture(autouse=True)
    def setup_done(self, app):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")

    def test_dashboard_redirects_to_login(self, client):
        response = client.get("/dashboard")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_live_redirects_to_login(self, client):
        response = client.get("/live")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_recordings_redirects_to_login(self, client):
        response = client.get("/recordings")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_settings_redirects_to_login(self, client):
        response = client.get("/settings")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_dashboard_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/dashboard")
        assert response.status_code == 200

    def test_live_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/live")
        assert response.status_code == 200

    def test_recordings_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/recordings")
        assert response.status_code == 200

    def test_settings_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/settings")
        assert response.status_code == 200
