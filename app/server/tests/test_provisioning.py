"""
Tests for WiFi provisioning API endpoints.
"""
import os
import pytest
from unittest.mock import patch, MagicMock


class TestSetupStatus:
    """Tests for GET /api/v1/setup/status."""

    def test_returns_not_complete_initially(self, client):
        response = client.get("/api/v1/setup/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["setup_complete"] is False

    def test_returns_complete_after_stamp(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.get("/api/v1/setup/status")
        data = response.get_json()
        assert data["setup_complete"] is True


class TestWifiScan:
    """Tests for GET /api/v1/setup/wifi/scan."""

    def test_blocked_after_setup_complete(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.get("/api/v1/setup/wifi/scan")
        assert response.status_code == 403

    @patch("monitor.provisioning.subprocess.run")
    def test_returns_networks(self, mock_run, client):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="MyNetwork:85:WPA2\nOtherNet:60:WPA2\nMyNetwork:70:WPA2\n",
            stderr="",
        )
        response = client.get("/api/v1/setup/wifi/scan")
        assert response.status_code == 200
        data = response.get_json()
        networks = data["networks"]
        assert len(networks) == 2  # Deduplicated
        assert networks[0]["ssid"] == "MyNetwork"
        assert networks[0]["signal"] == 85  # Kept strongest

    @patch("monitor.provisioning.subprocess.run")
    def test_scan_failure(self, mock_run, client):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: WiFi not available",
        )
        response = client.get("/api/v1/setup/wifi/scan")
        assert response.status_code == 500


class TestWifiConnect:
    """Tests for POST /api/v1/setup/wifi/connect."""

    def test_blocked_after_setup_complete(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.post("/api/v1/setup/wifi/connect", json={
            "ssid": "test", "password": "pass"
        })
        assert response.status_code == 403

    def test_requires_ssid(self, client):
        response = client.post("/api/v1/setup/wifi/connect", json={
            "ssid": "", "password": "pass"
        })
        assert response.status_code == 400

    def test_requires_password(self, client):
        response = client.post("/api/v1/setup/wifi/connect", json={
            "ssid": "test", "password": ""
        })
        assert response.status_code == 400

    @patch("monitor.provisioning.subprocess.run")
    def test_connect_success(self, mock_run, client):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        response = client.post("/api/v1/setup/wifi/connect", json={
            "ssid": "MyNetwork", "password": "secret123"
        })
        assert response.status_code == 200
        data = response.get_json()
        assert "Connected" in data["message"]

    @patch("monitor.provisioning.subprocess.run")
    def test_connect_wrong_password(self, mock_run, client):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Secrets were required"
        )
        response = client.post("/api/v1/setup/wifi/connect", json={
            "ssid": "MyNetwork", "password": "wrong"
        })
        assert response.status_code == 401


class TestSetAdminPassword:
    """Tests for POST /api/v1/setup/admin."""

    def test_blocked_after_setup_complete(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.post("/api/v1/setup/admin", json={
            "password": "newpassword123"
        })
        assert response.status_code == 403

    def test_password_too_short(self, client):
        response = client.post("/api/v1/setup/admin", json={
            "password": "short"
        })
        assert response.status_code == 400

    def test_requires_json(self, client):
        response = client.post("/api/v1/setup/admin")
        assert response.status_code == 400

    def test_updates_admin_password(self, app, client):
        # Create admin user first
        from monitor.auth import hash_password
        from monitor.models import User
        admin = User(
            id="user-admin-default",
            username="admin",
            password_hash=hash_password("admin"),
            role="admin",
            created_at="2026-01-01T00:00:00Z",
        )
        app.store.save_user(admin)

        response = client.post("/api/v1/setup/admin", json={
            "password": "newpassword123"
        })
        assert response.status_code == 200

        # Verify password was changed
        updated = app.store.get_user_by_username("admin")
        from monitor.auth import check_password
        assert check_password("newpassword123", updated.password_hash)


class TestSetupComplete:
    """Tests for POST /api/v1/setup/complete."""

    def test_blocked_after_setup_complete(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.post("/api/v1/setup/complete")
        assert response.status_code == 403

    @patch("monitor.provisioning.subprocess.run")
    def test_marks_setup_done(self, mock_run, app, client):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        response = client.post("/api/v1/setup/complete")
        assert response.status_code == 200

        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        assert os.path.isfile(stamp)

    @patch("monitor.provisioning.subprocess.run")
    def test_returns_ip_address(self, mock_run, app, client):
        def side_effect(cmd, **kwargs):
            if "show" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="IP4.ADDRESS[1]:192.168.1.42/24\n",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        response = client.post("/api/v1/setup/complete")
        assert response.status_code == 200
        data = response.get_json()
        assert data["ip"] == "192.168.1.42"
