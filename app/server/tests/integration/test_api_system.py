# REQ: SWR-032, SWR-020, SWR-018, SWR-023, SWR-024, SWR-034, SWR-045; RISK: RISK-005, RISK-006, RISK-011, RISK-012, RISK-017, RISK-019, RISK-020; SEC: SC-004, SC-006, SC-011, SC-012, SC-017, SC-020, SC-021; TEST: TC-010, TC-015, TC-022, TC-023, TC-029, TC-032, TC-041, TC-042
"""Tests for the system API."""

import io
import json
from unittest.mock import MagicMock, patch

from monitor.models import Camera, Settings, User, WebhookDestination


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
        # firmware_version is a live read of /etc/os-release VERSION_ID
        # via release_version() since 1.4.3 (docs/architecture/versioning.md).
        # Empty string on CI runners; only assert it's a string.
        assert isinstance(data["firmware_version"], str)
        assert data["uptime"]["seconds"] == 7200


class TestNetworkEndpoint:
    """Test GET /api/v1/system/network."""

    def test_allows_unauthenticated_access(self, client):
        response = client.get(
            "/api/v1/system/network",
            base_url="https://192.168.1.42:5443",
        )
        assert response.status_code == 200
        assert response.get_json() == {
            "server_url": "https://192.168.1.42:5443/",
            "ip": "192.168.1.42",
            "port": 5443,
            "source": "request_host",
        }

    def test_authenticated_fetch_logs_audit(self, app, client):
        app.audit = MagicMock()
        serializer = app.session_interface.get_signing_serializer(app)
        cookie_value = serializer.dumps(
            {
                "user_id": "user-admin",
                "username": "admin",
            }
        )
        client.set_cookie(
            app.config["SESSION_COOKIE_NAME"],
            cookie_value,
            domain="192.168.1.42",
        )

        response = client.get(
            "/api/v1/system/network",
            base_url="https://192.168.1.42:5443",
        )

        assert response.status_code == 200
        app.audit.log_event.assert_called_once_with(
            "SYSTEM_NETWORK_FALLBACK_VIEWED",
            user="admin",
            ip="127.0.0.1",
            detail="source=request_host",
        )


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


class TestTimeHealthEndpoints:
    def test_get_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.get("/api/v1/system/time/health").status_code == 403

    def test_get_returns_payload(self, app, logged_in_client):
        app.time_health_service = MagicMock()
        app.time_health_service.compute_health.return_value = {
            "state": "amber",
            "server": {
                "ntp_active": True,
                "ntp_synchronized": False,
                "unsynced_seconds": 15,
                "last_sync_time": "",
            },
            "cameras": [],
            "worst_camera": None,
            "worst_drift_seconds": None,
        }
        client = logged_in_client()
        resp = client.get("/api/v1/system/time/health")
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "amber"

    def test_resync_requires_target(self, logged_in_client):
        client = logged_in_client()
        resp = client.post("/api/v1/system/time/resync", json={})
        assert resp.status_code == 400

    def test_resync_success_logs_audit(self, app, logged_in_client):
        app.time_health_service = MagicMock()
        app.time_health_service.request_resync.return_value = (
            "System time resync requested",
            200,
            True,
        )
        app.audit = MagicMock()
        client = logged_in_client()
        app.audit.log_event.reset_mock()
        resp = client.post("/api/v1/system/time/resync", json={"target": "server"})
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "System time resync requested"
        app.audit.log_event.assert_called_once_with(
            "TIME_RESYNC_REQUESTED",
            user="admin",
            ip="127.0.0.1",
            detail="target=server",
        )


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
        app.tailscale_service.connect.return_value = (
            "https://login.tailscale.com/a/xxx",
            None,
        )
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

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    @patch("monitor.services.factory_reset_service.FactoryResetService._clear_wifi")
    def test_reset_succeeds(self, mock_wifi, mock_restart, app, logged_in_client):
        client = logged_in_client()
        resp = client.post("/api/v1/system/factory-reset")
        assert resp.status_code == 200
        assert "reset" in resp.get_json()["message"].lower()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
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
        resp = client.post(
            "/api/v1/system/factory-reset", json={"keep_recordings": True}
        )
        assert resp.status_code == 200
        # Recordings directory must still exist
        assert os.path.isfile(marker)

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    @patch("monitor.services.factory_reset_service.FactoryResetService._clear_wifi")
    @patch("monitor.services.factory_reset_service.FactoryResetService._log_audit")
    def test_factory_reset_logged(
        self, mock_log, mock_wifi, mock_restart, app, logged_in_client
    ):
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


class TestConfigBackup:
    """Test configuration backup endpoints."""

    PASSPHRASE = "correct horse battery staple"

    def _seed_backup_state(self, app):
        app.store.save_user(
            User(
                id="user-owner",
                username="owner",
                password_hash="hash-owner",
                role="admin",
                totp_secret="totp-owner",
            )
        )
        app.store.save_camera(
            Camera(
                id="cam-001",
                name="Front Door",
                location="Porch",
                status="online",
                pairing_secret="ab" * 32,
                cert_serial="SER-001",
            )
        )
        app.store.save_settings(
            Settings(
                hostname="backup-box",
                tailscale_auth_key="tskey-auth-test",
                webhook_destinations=[
                    WebhookDestination(
                        id="wh-001",
                        url="https://hooks.example.com/inbound",
                        auth_type="hmac",
                        secret="whsec-test",
                        event_classes=("motion",),
                    )
                ],
            )
        )
        config_dir = app.config["CONFIG_DIR"]
        certs_dir = app.config["CERTS_DIR"]
        with open(f"{config_dir}/hostname", "w") as handle:
            handle.write("backup-box\n")
        import os

        os.makedirs(f"{certs_dir}/cameras", exist_ok=True)
        with open(f"{certs_dir}/ca.crt", "w") as handle:
            handle.write("root-cert")
        with open(f"{certs_dir}/cameras/cam-001.crt", "w") as handle:
            handle.write("camera-cert")

    def _export_bundle(self, client):
        response = client.post(
            "/api/v1/system/backup/export",
            json={
                "passphrase": self.PASSPHRASE,
                "include_user_credentials": True,
                "include_webhook_secrets": True,
                "include_tailscale_auth_key": True,
            },
        )
        assert response.status_code == 200
        return response.data

    def test_backup_export_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        response = client.post(
            "/api/v1/system/backup/export",
            json={"passphrase": self.PASSPHRASE},
        )
        assert response.status_code == 403

    def test_backup_export_requires_auth(self, client):
        response = client.post(
            "/api/v1/system/backup/export",
            json={"passphrase": self.PASSPHRASE},
        )
        assert response.status_code == 401

    def test_backup_export_downloads_bundle(self, app, logged_in_client):
        self._seed_backup_state(app)
        client = logged_in_client()

        response = client.post(
            "/api/v1/system/backup/export",
            json={"passphrase": self.PASSPHRASE},
        )

        assert response.status_code == 200
        assert response.mimetype == "application/vnd.home-monitor.backup+json"
        assert "attachment" in response.headers["Content-Disposition"]
        bundle = json.loads(response.data)
        assert bundle["manifest"]["schema_version"] == 1

    def test_backup_preview_returns_summary(self, app, logged_in_client):
        self._seed_backup_state(app)
        client = logged_in_client()
        bundle_bytes = self._export_bundle(client)

        app.store.save_user(
            User(
                id="user-late",
                username="late-user",
                password_hash="hash-late",
                role="viewer",
            )
        )

        response = client.post(
            "/api/v1/system/backup/preview",
            data={
                "passphrase": self.PASSPHRASE,
                "file": (io.BytesIO(bundle_bytes), "config-backup.hmb"),
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["filename"] == "config-backup.hmb"
        assert data["preview"]["users"]["remove"] == 1

    def test_backup_import_restores_state(self, app, logged_in_client):
        self._seed_backup_state(app)
        client = logged_in_client()
        bundle_bytes = self._export_bundle(client)

        app.store.save_user(
            User(
                id="user-temp",
                username="temp-user",
                password_hash="hash-temp",
                role="viewer",
            )
        )
        app.store.save_camera(Camera(id="cam-999", name="Garage", status="offline"))
        settings = app.store.get_settings()
        settings.tailscale_auth_key = ""
        settings.webhook_destinations = []
        app.store.save_settings(settings)
        with open(f"{app.config['CONFIG_DIR']}/hostname", "w") as handle:
            handle.write("changed-host\n")
        with open(f"{app.config['CERTS_DIR']}/ca.crt", "w") as handle:
            handle.write("rotated-cert")

        response = client.post(
            "/api/v1/system/backup/import",
            data={
                "passphrase": self.PASSPHRASE,
                "file": (io.BytesIO(bundle_bytes), "config-backup.hmb"),
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["message"] == "Configuration restored"
        assert {user.username for user in app.store.get_users()} == {"admin", "owner"}
        assert [camera.id for camera in app.store.get_cameras()] == ["cam-001"]
        assert app.store.get_settings().tailscale_auth_key == "tskey-auth-test"
        assert app.store.get_settings().webhook_destinations[0].secret == "whsec-test"

    def test_backup_import_rejects_wrong_passphrase(self, app, logged_in_client):
        self._seed_backup_state(app)
        client = logged_in_client()
        bundle_bytes = self._export_bundle(client)

        response = client.post(
            "/api/v1/system/backup/import",
            data={
                "passphrase": "this is the wrong secret",
                "file": (io.BytesIO(bundle_bytes), "config-backup.hmb"),
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        assert response.get_json()["reason"] == "signature_mismatch"

    def test_backup_snapshots_lists_metadata(self, app, logged_in_client):
        self._seed_backup_state(app)
        client = logged_in_client()
        bundle_bytes = self._export_bundle(client)

        response = client.post(
            "/api/v1/system/backup/import",
            data={
                "passphrase": self.PASSPHRASE,
                "file": (io.BytesIO(bundle_bytes), "config-backup.hmb"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 200

        snapshots = client.get("/api/v1/system/backup/snapshots")
        assert snapshots.status_code == 200
        assert len(snapshots.get_json()["snapshots"]) == 1
