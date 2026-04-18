"""Tests for the system API."""

from unittest.mock import MagicMock, patch


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
    def test_returns_health_data(self, mock_health, logged_in_client):
        client = logged_in_client()
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
    def test_returns_system_info(self, mock_uptime, logged_in_client):
        client = logged_in_client()
        response = client.get("/api/v1/system/info")
        assert response.status_code == 200
        data = response.get_json()
        assert data["hostname"] == "home-monitor"
        assert data["firmware_version"] == "1.0.0"
        assert data["uptime"]["seconds"] == 7200


class TestSystemSummary:
    """Test GET /api/v1/system/summary."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/system/summary").status_code == 401

    def test_returns_summary_fields(self, app, logged_in_client):
        client = logged_in_client()
        resp = client.get("/api/v1/system/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        for field in ("state", "summary", "details", "deep_link"):
            assert field in data, f"Missing field: {field}"

    def test_viewer_can_access(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.get("/api/v1/system/summary").status_code == 200


class TestTailscaleEndpoints:
    """Test Tailscale VPN management endpoints."""

    def test_get_status_requires_auth(self, client):
        assert client.get("/api/v1/system/tailscale").status_code == 401

    def test_get_status_returns_config(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.get_status.return_value = {"connected": False}
        client = logged_in_client()
        resp = client.get("/api/v1/system/tailscale")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "config" in data
        assert "enabled" in data["config"]
        assert "has_auth_key" in data["config"]

    def test_connect_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.post("/api/v1/system/tailscale/connect").status_code == 403

    def test_connect_success(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.connect.return_value = (None, None)
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/connect")
        assert resp.status_code == 200
        assert "connected" in resp.get_json()["message"].lower()

    def test_connect_returns_auth_url(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.connect.return_value = ("https://login.tailscale.com/a/xxx", None)
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/connect")
        assert resp.status_code == 200
        assert "auth_url" in resp.get_json()

    def test_connect_error_returns_500(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.connect.return_value = (None, "tailscale not installed")
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/connect")
        assert resp.status_code == 500

    def test_disconnect_success(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.disconnect.return_value = (True, None)
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/disconnect")
        assert resp.status_code == 200

    def test_disconnect_error_returns_500(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.disconnect.return_value = (False, "failed")
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/disconnect")
        assert resp.status_code == 500

    def test_enable_success(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.enable.return_value = (True, None)
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/enable")
        assert resp.status_code == 200
        assert "enabled" in resp.get_json()["message"].lower()

    def test_enable_error_returns_500(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.enable.return_value = (False, "daemon not found")
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/enable")
        assert resp.status_code == 500

    def test_disable_success(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.disable.return_value = (True, None)
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/disable")
        assert resp.status_code == 200

    def test_disable_error_returns_500(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.disable.return_value = (False, "failed")
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/disable")
        assert resp.status_code == 500

    def test_apply_config_success(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.apply_config.return_value = (None, None)
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/apply-config")
        assert resp.status_code == 200

    def test_apply_config_auth_url(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.apply_config.return_value = ("https://ts/auth", None)
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/apply-config")
        assert resp.status_code == 200
        assert "auth_url" in resp.get_json()

    def test_apply_config_error_returns_500(self, app, logged_in_client):
        app.tailscale_service = MagicMock()
        app.tailscale_service.apply_config.return_value = (None, "config invalid")
        client = logged_in_client()
        resp = client.post("/api/v1/system/tailscale/apply-config")
        assert resp.status_code == 500


class TestFactoryReset:
    """Test POST /api/v1/system/factory-reset."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.post("/api/v1/system/factory-reset").status_code == 403

    def test_requires_auth(self, client):
        assert client.post("/api/v1/system/factory-reset").status_code == 401

    @patch("monitor.services.factory_reset_service.FactoryResetService._schedule_restart")
    @patch("monitor.services.factory_reset_service.FactoryResetService._clear_wifi")
    def test_reset_succeeds(self, mock_wifi, mock_restart, app, logged_in_client):
        client = logged_in_client()
        resp = client.post("/api/v1/system/factory-reset")
        assert resp.status_code == 200
        assert "reset" in resp.get_json()["message"].lower()

    @patch("monitor.services.factory_reset_service.FactoryResetService._schedule_restart")
    @patch("monitor.services.factory_reset_service.FactoryResetService._clear_wifi")
    def test_keep_recordings_flag(self, mock_wifi, mock_restart, app, logged_in_client):
        import os
        rec_dir = app.config["RECORDINGS_DIR"]
        os.makedirs(rec_dir, exist_ok=True)
        marker = os.path.join(rec_dir, "cam-001", "2026-04-01", "08-00-00.mp4")
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w") as f:
            f.write("fake")

        client = logged_in_client()
        resp = client.post("/api/v1/system/factory-reset",
                           json={"keep_recordings": True})
        assert resp.status_code == 200
        # Recordings directory must still exist
        assert os.path.isfile(marker)

    @patch("monitor.services.factory_reset_service.FactoryResetService._schedule_restart")
    @patch("monitor.services.factory_reset_service.FactoryResetService._clear_wifi")
    @patch("monitor.services.factory_reset_service.FactoryResetService._log_audit")
    def test_factory_reset_logged(self, mock_log, mock_wifi, mock_restart, app, logged_in_client):
        client = logged_in_client()
        resp = client.post("/api/v1/system/factory-reset")
        assert resp.status_code == 200
        # _log_audit is called before data is wiped so the event is always captured
        mock_log.assert_called_once_with(
            "FACTORY_RESET",
            requesting_user="admin",
            requesting_ip=mock_log.call_args.kwargs.get("requesting_ip", ""),
            detail="keep_recordings=False",
        )
