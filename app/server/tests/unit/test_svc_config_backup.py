# REQ: SWR-023, SWR-024, SWR-034, SWR-045; RISK: RISK-011, RISK-012, RISK-019, RISK-020; SEC: SC-011, SC-012, SC-017, SC-020, SC-021; TEST: TC-022, TC-023, TC-032, TC-041, TC-042
"""Unit tests for ConfigBackupService."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from monitor.models import Camera, Settings, User, WebhookDestination
from monitor.services.config_backup_service import (
    ConfigBackupError,
    ConfigBackupService,
)
from monitor.store import Store

PASSPHRASE = "correct horse battery staple"


@pytest.fixture
def backup_env(tmp_path):
    """Seed a realistic server config + cert state for backup tests."""
    data_dir = tmp_path
    config_dir = data_dir / "config"
    certs_dir = data_dir / "certs"
    config_dir.mkdir()
    (certs_dir / "cameras").mkdir(parents=True)

    store = Store(str(config_dir))
    store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash="hash-admin",
            role="admin",
            totp_secret="totp-admin",
        )
    )
    store.save_user(
        User(
            id="user-viewer",
            username="viewer",
            password_hash="hash-viewer",
            role="viewer",
        )
    )
    store.save_camera(
        Camera(
            id="cam-001",
            name="Front Door",
            location="Porch",
            status="online",
            cert_serial="SER-001",
            pairing_secret="ab" * 32,
        )
    )
    store.save_settings(
        Settings(
            hostname="home-monitor",
            tailscale_auth_key="tskey-auth-test",
            webhook_destinations=[
                WebhookDestination(
                    id="wh-001",
                    url="https://hooks.example.com/inbound",
                    auth_type="hmac",
                    secret="whsec-test",
                    event_classes=("motion",),
                    enabled=True,
                )
            ],
        )
    )
    (config_dir / "hostname").write_text("backup-box\n", encoding="utf-8")
    (certs_dir / "ca.crt").write_text("root-cert", encoding="utf-8")
    (certs_dir / "cameras" / "cam-001.crt").write_text(
        "camera-cert",
        encoding="utf-8",
    )

    service = ConfigBackupService(
        store=store,
        audit=MagicMock(),
        settings_service=MagicMock(),
        data_dir=str(data_dir),
        config_dir=str(config_dir),
        certs_dir=str(certs_dir),
    )
    return {
        "service": service,
        "store": store,
        "data_dir": data_dir,
        "config_dir": config_dir,
        "certs_dir": certs_dir,
    }


class TestConfigBackupExport:
    """Export + preview behavior."""

    def test_export_excludes_secrets_by_default(self, backup_env):
        service = backup_env["service"]

        filename, bundle_bytes, preview = service.export_bundle(passphrase=PASSPHRASE)
        bundle = json.loads(bundle_bytes)

        assert filename.endswith(".hmb")
        assert bundle["manifest"]["scope"]["camera_trust"] is True
        assert preview["counts"]["users"] == 2
        assert preview["counts"]["cameras"] == 1
        assert bundle["payload"]["users"][0]["password_hash"] == ""
        assert bundle["payload"]["users"][0]["totp_secret"] == ""
        assert bundle["payload"]["settings"]["config"]["tailscale_auth_key"] == ""
        assert (
            bundle["payload"]["settings"]["config"]["webhook_destinations"][0]["secret"]
            == ""
        )
        assert (
            bundle["payload"]["settings"]["config"]["webhook_destinations"][0][
                "enabled"
            ]
            is False
        )
        assert len(bundle["payload"]["camera_trust"]["entries"]) == 2

    def test_preview_reports_removals_and_secret_warnings(self, backup_env):
        service = backup_env["service"]
        store = backup_env["store"]

        _, bundle_bytes, _ = service.export_bundle(passphrase=PASSPHRASE)
        store.save_user(
            User(
                id="user-extra",
                username="late-user",
                password_hash="hash-extra",
                role="viewer",
            )
        )
        store.save_camera(Camera(id="cam-999", name="Garage", status="online"))

        preview = service.preview_bundle(bundle_bytes, passphrase=PASSPHRASE)

        assert preview["users"]["remove"] == 1
        assert preview["cameras"]["remove"] == 1
        assert any(
            "User credentials are excluded" in warning
            for warning in preview["warnings"]
        )
        assert any(
            "Webhook secrets are excluded" in warning for warning in preview["warnings"]
        )


class TestConfigBackupImport:
    """Restore behavior, validation, and rollback."""

    def test_full_round_trip_restores_state(self, backup_env):
        service = backup_env["service"]
        store = backup_env["store"]
        config_dir = backup_env["config_dir"]
        certs_dir = backup_env["certs_dir"]

        _, bundle_bytes, _ = service.export_bundle(
            passphrase=PASSPHRASE,
            options={
                "include_user_credentials": True,
                "include_webhook_secrets": True,
                "include_tailscale_auth_key": True,
            },
        )

        store.save_user(
            User(
                id="user-temp",
                username="temp-user",
                password_hash="hash-temp",
                role="viewer",
            )
        )
        store.save_camera(Camera(id="cam-002", name="Garage", status="offline"))
        settings = store.get_settings()
        settings.hostname = "changed-host"
        settings.tailscale_auth_key = ""
        settings.webhook_destinations = []
        store.save_settings(settings)
        (config_dir / "hostname").write_text("changed-host\n", encoding="utf-8")
        (certs_dir / "ca.crt").write_text("rotated-cert", encoding="utf-8")

        result = service.import_bundle(bundle_bytes, passphrase=PASSPHRASE)

        assert result["message"] == "Configuration restored"
        assert set(result["restored_components"]) == {
            "camera_trust",
            "cameras",
            "settings",
            "users",
        }
        assert {user.username for user in store.get_users()} == {"admin", "viewer"}
        assert store.get_user_by_username("admin").password_hash == "hash-admin"
        assert [camera.id for camera in store.get_cameras()] == ["cam-001"]
        restored_settings = store.get_settings()
        assert restored_settings.tailscale_auth_key == "tskey-auth-test"
        assert restored_settings.webhook_destinations[0].secret == "whsec-test"
        assert (config_dir / "hostname").read_text(
            encoding="utf-8"
        ).strip() == "backup-box"
        assert (certs_dir / "ca.crt").read_text(encoding="utf-8") == "root-cert"
        assert result["snapshot"]["bundle_created_at"]

    def test_rejects_tampered_bundle(self, backup_env):
        service = backup_env["service"]

        _, bundle_bytes, _ = service.export_bundle(passphrase=PASSPHRASE)
        bundle = json.loads(bundle_bytes)
        bundle["payload"]["users"][0]["username"] = "mallory"
        tampered_bytes = json.dumps(bundle).encode("utf-8")

        with pytest.raises(ConfigBackupError) as excinfo:
            service.preview_bundle(tampered_bytes, passphrase=PASSPHRASE)

        assert excinfo.value.reason == "signature_mismatch"

    def test_rejects_restore_scope_not_present_in_bundle(self, backup_env):
        service = backup_env["service"]

        _, bundle_bytes, _ = service.export_bundle(
            passphrase=PASSPHRASE,
            options={"users": False, "settings": False},
        )

        with pytest.raises(ConfigBackupError) as excinfo:
            service.import_bundle(
                bundle_bytes,
                passphrase=PASSPHRASE,
                restore_options={"users": True},
            )

        assert excinfo.value.reason == "invalid_restore_scope"

    def test_rolls_back_partial_restore_failure(self, backup_env, monkeypatch):
        service = backup_env["service"]
        store = backup_env["store"]
        config_dir = backup_env["config_dir"]

        _, bundle_bytes, _ = service.export_bundle(
            passphrase=PASSPHRASE,
            options={"include_user_credentials": True},
        )
        store.save_user(
            User(
                id="user-extra",
                username="extra",
                password_hash="hash-extra",
                role="viewer",
            )
        )
        current_users_json = (config_dir / "users.json").read_text(encoding="utf-8")
        current_cameras_json = (config_dir / "cameras.json").read_text(encoding="utf-8")

        original_replace_file = service._replace_file

        def crash_on_camera(path, content):
            original_replace_file(path, content)
            if path.name == "cameras.json":
                raise RuntimeError("disk full")

        monkeypatch.setattr(service, "_replace_file", crash_on_camera)

        with pytest.raises(ConfigBackupError) as excinfo:
            service.import_bundle(bundle_bytes, passphrase=PASSPHRASE)

        assert excinfo.value.reason == "import_failed"
        assert (config_dir / "users.json").read_text(
            encoding="utf-8"
        ) == current_users_json
        assert (config_dir / "cameras.json").read_text(
            encoding="utf-8"
        ) == current_cameras_json
