"""Tests for the settings API."""
from unittest.mock import patch
from monitor.auth import hash_password


def _login(app, client, role="admin"):
    """Helper: create user and login."""
    from monitor.models import User
    app.store.save_user(User(
        id="user-admin",
        username="admin",
        password_hash=hash_password("pass"),
        role=role,
    ))
    client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "pass",
    })


class TestGetSettings:
    """Test GET /api/v1/settings."""

    def test_requires_auth(self, client):
        response = client.get("/api/v1/settings")
        assert response.status_code == 401

    def test_returns_settings(self, app, client):
        _login(app, client)
        response = client.get("/api/v1/settings")
        assert response.status_code == 200
        data = response.get_json()
        assert data["hostname"] == "home-monitor"
        assert data["timezone"] == "Europe/Dublin"
        assert data["clip_duration_seconds"] == 180
        assert data["session_timeout_minutes"] == 30
        assert data["storage_threshold_percent"] == 90
        assert data["firmware_version"] == "1.0.0"
        assert data["setup_completed"] is False

    def test_viewer_can_read_settings(self, app, client):
        _login(app, client, role="viewer")
        response = client.get("/api/v1/settings")
        assert response.status_code == 200


class TestUpdateSettings:
    """Test PUT /api/v1/settings."""

    def test_requires_auth(self, client):
        response = client.put("/api/v1/settings", json={"hostname": "new"})
        assert response.status_code == 401

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        response = client.put("/api/v1/settings", json={"hostname": "new"})
        assert response.status_code == 403

    def test_requires_json_body(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings")
        assert response.status_code == 400

    def test_rejects_unknown_fields(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"bogus": "value"})
        assert response.status_code == 400
        assert "Unknown fields" in response.get_json()["error"]

    def test_update_hostname(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"hostname": "my-server"})
        assert response.status_code == 200

        # Verify persisted
        response = client.get("/api/v1/settings")
        assert response.get_json()["hostname"] == "my-server"

    def test_update_timezone(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"timezone": "America/New_York"})
        assert response.status_code == 200
        response = client.get("/api/v1/settings")
        assert response.get_json()["timezone"] == "America/New_York"

    def test_update_multiple_fields(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={
            "hostname": "rpi-server",
            "clip_duration_seconds": 120,
            "storage_threshold_percent": 85,
        })
        assert response.status_code == 200
        data = client.get("/api/v1/settings").get_json()
        assert data["hostname"] == "rpi-server"
        assert data["clip_duration_seconds"] == 120
        assert data["storage_threshold_percent"] == 85

    def test_update_session_timeout(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"session_timeout_minutes": 60})
        assert response.status_code == 200
        data = client.get("/api/v1/settings").get_json()
        assert data["session_timeout_minutes"] == 60


class TestSettingsValidation:
    """Test input validation for PUT /api/v1/settings."""

    def test_storage_threshold_too_low(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"storage_threshold_percent": 10})
        assert response.status_code == 400

    def test_storage_threshold_too_high(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"storage_threshold_percent": 100})
        assert response.status_code == 400

    def test_clip_duration_too_short(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"clip_duration_seconds": 5})
        assert response.status_code == 400

    def test_clip_duration_too_long(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"clip_duration_seconds": 9999})
        assert response.status_code == 400

    def test_session_timeout_too_short(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"session_timeout_minutes": 1})
        assert response.status_code == 400

    def test_session_timeout_too_long(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"session_timeout_minutes": 5000})
        assert response.status_code == 400

    def test_hostname_empty(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"hostname": ""})
        assert response.status_code == 400

    def test_hostname_too_long(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"hostname": "a" * 64})
        assert response.status_code == 400

    def test_timezone_invalid(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"timezone": "NotATimezone"})
        assert response.status_code == 400

    def test_cannot_update_firmware_version(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"firmware_version": "2.0.0"})
        assert response.status_code == 400

    def test_cannot_update_setup_completed(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/settings", json={"setup_completed": True})
        assert response.status_code == 400


class TestSettingsAuditLog:
    """Test that settings changes are audit logged."""

    def test_update_logs_audit_event(self, app, client):
        _login(app, client)
        client.put("/api/v1/settings", json={"hostname": "new-host"})
        events = app.audit.get_events(limit=10, event_type="SETTINGS_UPDATED")
        assert len(events) >= 1
        assert "hostname" in events[0]["detail"]
