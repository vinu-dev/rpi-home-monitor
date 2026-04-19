"""Tests for the settings API."""

from unittest.mock import MagicMock, patch


class TestGetSettings:
    """Test GET /api/v1/settings."""

    def test_requires_auth(self, client):
        response = client.get("/api/v1/settings")
        assert response.status_code == 401

    def test_returns_settings(self, logged_in_client):
        client = logged_in_client()
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

    def test_viewer_can_read_settings(self, logged_in_client):
        client = logged_in_client("viewer")
        response = client.get("/api/v1/settings")
        assert response.status_code == 200


class TestUpdateSettings:
    """Test PUT /api/v1/settings."""

    def test_requires_auth(self, client):
        response = client.put("/api/v1/settings", json={"hostname": "new"})
        assert response.status_code == 401

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        response = client.put("/api/v1/settings", json={"hostname": "new"})
        assert response.status_code == 403

    def test_requires_json_body(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings")
        assert response.status_code == 400

    def test_rejects_unknown_fields(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"bogus": "value"})
        assert response.status_code == 400
        assert "Unknown fields" in response.get_json()["error"]

    def test_update_hostname(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"hostname": "my-server"})
        assert response.status_code == 200

        # Verify persisted
        response = client.get("/api/v1/settings")
        assert response.get_json()["hostname"] == "my-server"

    def test_update_timezone(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"timezone": "America/New_York"})
        assert response.status_code == 200
        response = client.get("/api/v1/settings")
        assert response.get_json()["timezone"] == "America/New_York"

    def test_update_multiple_fields(self, logged_in_client):
        client = logged_in_client()
        response = client.put(
            "/api/v1/settings",
            json={
                "hostname": "rpi-server",
                "clip_duration_seconds": 120,
                "storage_threshold_percent": 85,
            },
        )
        assert response.status_code == 200
        data = client.get("/api/v1/settings").get_json()
        assert data["hostname"] == "rpi-server"
        assert data["clip_duration_seconds"] == 120
        assert data["storage_threshold_percent"] == 85

    def test_update_session_timeout(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"session_timeout_minutes": 60})
        assert response.status_code == 200
        data = client.get("/api/v1/settings").get_json()
        assert data["session_timeout_minutes"] == 60


class TestSettingsValidation:
    """Test input validation for PUT /api/v1/settings."""

    def test_storage_threshold_too_low(self, logged_in_client):
        client = logged_in_client()
        response = client.put(
            "/api/v1/settings", json={"storage_threshold_percent": 10}
        )
        assert response.status_code == 400

    def test_storage_threshold_too_high(self, logged_in_client):
        client = logged_in_client()
        response = client.put(
            "/api/v1/settings", json={"storage_threshold_percent": 100}
        )
        assert response.status_code == 400

    def test_clip_duration_too_short(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"clip_duration_seconds": 5})
        assert response.status_code == 400

    def test_clip_duration_too_long(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"clip_duration_seconds": 9999})
        assert response.status_code == 400

    def test_session_timeout_too_short(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"session_timeout_minutes": 1})
        assert response.status_code == 400

    def test_session_timeout_too_long(self, logged_in_client):
        client = logged_in_client()
        response = client.put(
            "/api/v1/settings", json={"session_timeout_minutes": 5000}
        )
        assert response.status_code == 400

    def test_hostname_empty(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"hostname": ""})
        assert response.status_code == 400

    def test_hostname_too_long(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"hostname": "a" * 64})
        assert response.status_code == 400

    def test_timezone_invalid(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"timezone": "NotATimezone"})
        assert response.status_code == 400

    def test_cannot_update_firmware_version(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"firmware_version": "2.0.0"})
        assert response.status_code == 400

    def test_cannot_update_setup_completed(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/settings", json={"setup_completed": True})
        assert response.status_code == 400


class TestSettingsAuditLog:
    """Test that settings changes are audit logged."""

    def test_update_logs_audit_event(self, app, logged_in_client):
        client = logged_in_client()
        client.put("/api/v1/settings", json={"hostname": "new-host"})
        events = app.audit.get_events(limit=10, event_type="SETTINGS_UPDATED")
        assert len(events) >= 1
        assert "hostname" in events[0]["detail"]


class TestTimeEndpoints:
    """Tests for GET/POST /api/v1/settings/time."""

    def test_get_time_requires_auth(self, client):
        assert client.get("/api/v1/settings/time").status_code == 401

    def test_viewer_can_get_time(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.get("/api/v1/settings/time").status_code == 200

    @patch("monitor.services.settings_service.subprocess.run")
    def test_get_time_returns_expected_fields(self, mock_run, logged_in_client):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Timezone=Europe/Dublin\nNTP=yes\nNTPSynchronized=yes\nTimeUSec=2026-01-01\nRTCTimeUSec=2026-01-01\n",
        )
        client = logged_in_client()
        resp = client.get("/api/v1/settings/time")
        assert resp.status_code == 200
        data = resp.get_json()
        for key in (
            "timezone",
            "ntp_mode",
            "ntp_active",
            "ntp_synchronized",
            "system_time",
            "rtc_time",
        ):
            assert key in data

    def test_set_time_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert (
            client.post(
                "/api/v1/settings/time", json={"time": "2026-01-01T12:00:00"}
            ).status_code
            == 403
        )

    def test_set_time_requires_auth(self, client):
        assert (
            client.post(
                "/api/v1/settings/time", json={"time": "2026-01-01T12:00:00"}
            ).status_code
            == 401
        )

    def test_set_time_requires_json_body(self, logged_in_client):
        client = logged_in_client()
        assert client.post("/api/v1/settings/time").status_code == 400

    @patch("monitor.services.settings_service.subprocess.run")
    def test_set_time_rejects_when_ntp_not_manual(
        self, mock_run, app, logged_in_client
    ):
        # Default ntp_mode is "auto", so manual time set should be rejected
        client = logged_in_client()
        resp = client.post(
            "/api/v1/settings/time", json={"time": "2026-01-01T12:00:00"}
        )
        assert resp.status_code == 409
        assert "manual" in resp.get_json()["error"].lower()

    @patch("monitor.services.settings_service.subprocess.run")
    def test_set_time_succeeds_when_ntp_manual(self, mock_run, app, logged_in_client):
        settings = app.store.get_settings()
        settings.ntp_mode = "manual"
        app.store.save_settings(settings)
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        client = logged_in_client()
        resp = client.post(
            "/api/v1/settings/time", json={"time": "2026-01-01T12:00:00Z"}
        )
        assert resp.status_code == 200
        assert "updated" in resp.get_json()["message"].lower()

    @patch("monitor.services.settings_service.subprocess.run")
    def test_set_time_invalid_format_returns_400(self, mock_run, app, logged_in_client):
        settings = app.store.get_settings()
        settings.ntp_mode = "manual"
        app.store.save_settings(settings)
        client = logged_in_client()
        resp = client.post("/api/v1/settings/time", json={"time": "not-a-time"})
        assert resp.status_code == 400

    @patch("monitor.services.settings_service.subprocess.run")
    def test_set_time_timedatectl_failure_returns_500(
        self, mock_run, app, logged_in_client
    ):
        settings = app.store.get_settings()
        settings.ntp_mode = "manual"
        app.store.save_settings(settings)
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Permission denied"
        )
        client = logged_in_client()
        resp = client.post(
            "/api/v1/settings/time", json={"time": "2026-01-01T12:00:00"}
        )
        assert resp.status_code == 500


class TestWifiEndpoints:
    """Tests for GET/POST /api/v1/settings/wifi."""

    def test_get_wifi_requires_auth(self, client):
        assert client.get("/api/v1/settings/wifi").status_code == 401

    def test_get_wifi_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.get("/api/v1/settings/wifi").status_code == 403

    @patch("monitor.services.settings_service.subprocess.run")
    def test_get_wifi_returns_expected_fields(self, mock_run, logged_in_client):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        client = logged_in_client()
        resp = client.get("/api/v1/settings/wifi")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "current_ssid" in data
        assert "networks" in data

    def test_set_wifi_requires_auth(self, client):
        assert (
            client.post(
                "/api/v1/settings/wifi", json={"ssid": "x", "password": "y"}
            ).status_code
            == 401
        )

    def test_set_wifi_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert (
            client.post(
                "/api/v1/settings/wifi", json={"ssid": "x", "password": "y"}
            ).status_code
            == 403
        )

    def test_set_wifi_requires_json_body(self, logged_in_client):
        client = logged_in_client()
        assert client.post("/api/v1/settings/wifi").status_code == 400

    @patch("monitor.services.settings_service.subprocess.run")
    def test_set_wifi_empty_ssid_rejected(self, mock_run, logged_in_client):
        client = logged_in_client()
        resp = client.post(
            "/api/v1/settings/wifi", json={"ssid": "", "password": "pass"}
        )
        assert resp.status_code == 400

    @patch("monitor.services.settings_service.subprocess.run")
    def test_set_wifi_success(self, mock_run, logged_in_client):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        client = logged_in_client()
        resp = client.post(
            "/api/v1/settings/wifi", json={"ssid": "MyNetwork", "password": "secret123"}
        )
        assert resp.status_code == 200
        assert "message" in resp.get_json()

    @patch("monitor.services.settings_service.subprocess.run")
    def test_set_wifi_nmcli_failure_returns_error(self, mock_run, logged_in_client):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="No network found"
        )
        client = logged_in_client()
        resp = client.post(
            "/api/v1/settings/wifi", json={"ssid": "BadNet", "password": "pass"}
        )
        assert resp.status_code != 200
