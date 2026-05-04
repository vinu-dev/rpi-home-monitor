# REQ: SWR-024, SWR-057; RISK: RISK-012, RISK-017, RISK-020; SEC: SC-012, SC-020; TEST: TC-023, TC-041, TC-049
"""Integration tests for the offsite-backup settings API."""

from unittest.mock import MagicMock


class TestGetOffsiteBackupSettings:
    def test_requires_auth(self, client):
        resp = client.get("/api/v1/settings/offsite-backup")
        assert resp.status_code == 401

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        resp = client.get("/api/v1/settings/offsite-backup")
        assert resp.status_code == 403

    def test_returns_defaults_for_admin(self, logged_in_client):
        client = logged_in_client()
        resp = client.get("/api/v1/settings/offsite-backup")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["enabled"] is False
        assert data["queue_size"] == 0
        assert "secret_access_key" not in data


class TestUpdateOffsiteBackupSettings:
    def test_requires_json_body(self, logged_in_client):
        client = logged_in_client()
        resp = client.put("/api/v1/settings/offsite-backup")
        assert resp.status_code == 400

    def test_persists_settings_and_redacts_secret(self, app, logged_in_client):
        client = logged_in_client()
        resp = client.put(
            "/api/v1/settings/offsite-backup",
            json={
                "enabled": True,
                "endpoint": "minio.example.com:9000",
                "bucket": "hm-backups",
                "access_key_id": "AKIATEST",
                "secret_access_key": "very-secret-value",
                "prefix": "backups/home-monitor",
                "retention_days": 30,
                "bandwidth_cap_mbps": 5,
            },
        )
        assert resp.status_code == 200

        saved = app.store.get_settings()
        assert saved.offsite_backup_enabled is True
        assert saved.offsite_backup_bucket == "hm-backups"
        assert saved.offsite_backup_secret_access_key == "very-secret-value"

        get_resp = client.get("/api/v1/settings/offsite-backup")
        data = get_resp.get_json()
        assert data["secret_configured"] is True
        assert "secret_access_key" not in data

    def test_rejects_insecure_endpoint(self, logged_in_client):
        client = logged_in_client()
        resp = client.put(
            "/api/v1/settings/offsite-backup",
            json={
                "enabled": True,
                "endpoint": "http://minio.example.com:9000",
                "bucket": "hm-backups",
                "access_key_id": "AKIATEST",
                "secret_access_key": "very-secret-value",
            },
        )
        assert resp.status_code == 400
        assert "HTTPS" in resp.get_json()["error"]


class TestOffsiteBackupConnectionProbe:
    def test_test_connection_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        resp = client.post("/api/v1/settings/offsite-backup/test-connection", json={})
        assert resp.status_code == 403

    def test_test_connection_uses_saved_secret_when_form_is_blank(
        self, app, logged_in_client
    ):
        settings = app.store.get_settings()
        settings.offsite_backup_enabled = True
        settings.offsite_backup_endpoint = "minio.example.com:9000"
        settings.offsite_backup_bucket = "hm-backups"
        settings.offsite_backup_access_key_id = "AKIATEST"
        settings.offsite_backup_secret_access_key = "saved-secret"
        app.store.save_settings(settings)

        fake_client = MagicMock()
        app.offsite_backup_service._client_factory = lambda _config: fake_client

        client = logged_in_client()
        resp = client.post(
            "/api/v1/settings/offsite-backup/test-connection",
            json={
                "enabled": True,
                "endpoint": "minio.example.com:9000",
                "bucket": "hm-backups",
                "access_key_id": "AKIATEST",
                "secret_access_key": "",
            },
        )

        assert resp.status_code == 200
        fake_client.write_probe.assert_called_once()
        fake_client.delete_object.assert_called_once()
