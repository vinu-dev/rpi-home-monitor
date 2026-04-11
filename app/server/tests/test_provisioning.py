"""
Tests for WiFi provisioning API endpoints.
"""

import os
from unittest.mock import MagicMock, patch


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


class TestWifiSave:
    """Tests for POST /api/v1/setup/wifi/save."""

    def test_blocked_after_setup_complete(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.post(
            "/api/v1/setup/wifi/save", json={"ssid": "test", "password": "pass"}
        )
        assert response.status_code == 403

    def test_requires_ssid(self, client):
        response = client.post(
            "/api/v1/setup/wifi/save", json={"ssid": "", "password": "pass"}
        )
        assert response.status_code == 400

    def test_requires_password(self, client):
        response = client.post(
            "/api/v1/setup/wifi/save", json={"ssid": "test", "password": ""}
        )
        assert response.status_code == 400

    def test_requires_json(self, client):
        response = client.post("/api/v1/setup/wifi/save")
        assert response.status_code == 400

    def test_saves_credentials(self, client):
        """WiFi save stores creds in memory without connecting."""
        response = client.post(
            "/api/v1/setup/wifi/save",
            json={"ssid": "MyNetwork", "password": "secret123"},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "saved" in data["message"].lower()
        assert "MyNetwork" in data["message"]

    def test_does_not_call_nmcli(self, client):
        """WiFi save should NOT invoke nmcli (no actual connection)."""
        with patch("monitor.provisioning.subprocess.run") as mock_run:
            response = client.post(
                "/api/v1/setup/wifi/save",
                json={"ssid": "MyNetwork", "password": "secret123"},
            )
            assert response.status_code == 200
            mock_run.assert_not_called()


class TestSetAdminPassword:
    """Tests for POST /api/v1/setup/admin."""

    def test_blocked_after_setup_complete(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.post(
            "/api/v1/setup/admin", json={"password": "newpassword123"}
        )
        assert response.status_code == 403

    def test_password_too_short(self, client):
        response = client.post("/api/v1/setup/admin", json={"password": "short"})
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

        response = client.post(
            "/api/v1/setup/admin", json={"password": "newpassword123"}
        )
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

    def test_requires_saved_wifi(self, client):
        """Complete fails if WiFi credentials were not saved first."""
        # Reset pending WiFi
        from monitor.provisioning import _pending_wifi

        _pending_wifi["ssid"] = ""
        _pending_wifi["password"] = ""

        response = client.post("/api/v1/setup/complete")
        assert response.status_code == 400
        data = response.get_json()
        assert "WiFi" in data["error"]

    @patch("monitor.provisioning.threading.Timer")
    @patch("monitor.provisioning.subprocess.run")
    def test_full_flow_saves_then_completes(self, mock_run, mock_timer, app, client):
        """Full flow: save WiFi → save admin password → complete."""
        # Step 1: Save WiFi
        response = client.post(
            "/api/v1/setup/wifi/save",
            json={"ssid": "HomeWiFi", "password": "wifipass123"},
        )
        assert response.status_code == 200

        # Step 2: Complete (connects WiFi + writes stamp)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="IP4.ADDRESS[1]:192.168.1.42/24\n",
            stderr="",
        )
        response = client.post("/api/v1/setup/complete")
        assert response.status_code == 200

        # Verify stamp file written
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        assert os.path.isfile(stamp)

        # Verify response has IP + hostname (dynamic, not hardcoded)
        data = response.get_json()
        assert data["ip"] == "192.168.1.42"
        assert data["hostname"].endswith(".local")
        assert len(data["hostname"]) > len(".local")

    @patch("monitor.provisioning.threading.Timer")
    @patch("monitor.provisioning.subprocess.run")
    def test_connects_wifi_at_complete(self, mock_run, mock_timer, app, client):
        """WiFi nmcli connect is called at /complete, not at /wifi/save."""
        # Save WiFi credentials
        client.post(
            "/api/v1/setup/wifi/save",
            json={"ssid": "HomeWiFi", "password": "wifipass123"},
        )

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        client.post("/api/v1/setup/complete")

        # Verify nmcli was called with the saved credentials
        connect_calls = [c for c in mock_run.call_args_list if "connect" in str(c)]
        assert len(connect_calls) >= 1
        cmd = connect_calls[0][0][0]
        assert "HomeWiFi" in cmd

    @patch("monitor.provisioning.threading.Timer")
    @patch("monitor.provisioning.subprocess.run")
    def test_delays_hotspot_stop(self, mock_run, mock_timer, app, client):
        """Hotspot stop is scheduled via Timer, not called immediately."""
        client.post(
            "/api/v1/setup/wifi/save",
            json={"ssid": "HomeWiFi", "password": "wifipass123"},
        )
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        client.post("/api/v1/setup/complete")

        # Timer should be created with 15-second delay
        mock_timer.assert_called_once()
        args = mock_timer.call_args
        assert args[0][0] == 15.0  # 15 second delay
        mock_timer.return_value.start.assert_called_once()

    @patch("monitor.provisioning.subprocess.run")
    def test_wifi_connect_failure_returns_error(self, mock_run, app, client):
        """If WiFi connect fails at /complete, return error (hotspot stays up)."""
        client.post(
            "/api/v1/setup/wifi/save",
            json={"ssid": "WrongNetwork", "password": "wrongpass"},
        )

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Secrets were required but not provided",
        )
        response = client.post("/api/v1/setup/complete")
        assert response.status_code == 401

        # Stamp file should NOT be written on failure
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        assert not os.path.isfile(stamp)

    @patch("monitor.provisioning.threading.Timer")
    @patch("monitor.provisioning.subprocess.run")
    def test_clears_saved_credentials_on_success(
        self, mock_run, mock_timer, app, client
    ):
        """Saved WiFi credentials are cleared from memory after success."""
        from monitor.provisioning import _pending_wifi

        client.post(
            "/api/v1/setup/wifi/save",
            json={"ssid": "HomeWiFi", "password": "wifipass123"},
        )
        assert _pending_wifi["ssid"] == "HomeWiFi"

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        client.post("/api/v1/setup/complete")

        # Credentials should be cleared
        assert _pending_wifi["ssid"] == ""
        assert _pending_wifi["password"] == ""
