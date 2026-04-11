"""Tests for the system API."""

from unittest.mock import patch

from monitor.auth import hash_password


def _login(app, client):
    """Helper: create admin user and login."""
    from monitor.models import User

    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash=hash_password("pass"),
            role="admin",
        )
    )
    client.post(
        "/api/v1/auth/login",
        json={
            "username": "admin",
            "password": "pass",
        },
    )


class TestHealthEndpoint:
    """Test GET /api/v1/system/health."""

    def test_requires_auth(self, client):
        response = client.get("/api/v1/system/health")
        assert response.status_code == 401

    @patch(
        "monitor.api.system.get_health_summary",
        return_value={
            "cpu_temp_c": 55.0,
            "cpu_usage_percent": 25.0,
            "memory": {
                "total_mb": 4096,
                "used_mb": 2048,
                "free_mb": 2048,
                "percent": 50.0,
            },
            "disk": {"total_gb": 100, "used_gb": 40, "free_gb": 60, "percent": 40.0},
            "uptime": {"seconds": 3600, "display": "1h 0m"},
            "warnings": [],
            "status": "healthy",
        },
    )
    def test_returns_health_data(self, mock_health, app, client):
        _login(app, client)
        response = client.get("/api/v1/system/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"
        assert data["cpu_temp_c"] == 55.0
        assert "memory" in data
        assert "disk" in data


class TestInfoEndpoint:
    """Test GET /api/v1/system/info."""

    def test_requires_auth(self, client):
        response = client.get("/api/v1/system/info")
        assert response.status_code == 401

    @patch(
        "monitor.api.system.get_uptime",
        return_value={"seconds": 7200, "display": "2h 0m"},
    )
    def test_returns_system_info(self, mock_uptime, app, client):
        _login(app, client)
        response = client.get("/api/v1/system/info")
        assert response.status_code == 200
        data = response.get_json()
        assert data["hostname"] == "home-monitor"
        assert data["firmware_version"] == "1.0.0"
        assert data["uptime"]["seconds"] == 7200
