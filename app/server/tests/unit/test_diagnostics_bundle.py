# REQ: SWR-068, SWR-069, SWR-070; RISK: RISK-020, RISK-026; SEC: SC-020, SC-025; TEST: TC-055
"""Unit tests for diagnostics bundle assembly."""

from __future__ import annotations

import json
import tarfile

import pytest

from monitor.models import Camera, Settings, User, WebhookDestination
from monitor.services.diagnostics_bundle import DiagnosticsBundleError


def _load_bundle(path: str) -> tuple[list[str], dict, dict[str, str]]:
    members: list[str] = []
    files: dict[str, str] = {}
    manifest = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            members.append(member.name)
            if not member.isfile():
                continue
            data = archive.extractfile(member).read()
            text = data.decode("utf-8", errors="replace")
            files[member.name] = text
            if member.name.endswith("/manifest.json"):
                manifest = json.loads(text)
    return members, manifest, files


def _seed_secret_state(app) -> None:
    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash="hash-secret-value",
            role="admin",
            totp_secret="totp-secret-value",
            recovery_code_hashes=["recovery-secret-value"],
        )
    )
    app.store.save_camera(
        Camera(
            id="cam-001",
            name="Front Door",
            pairing_secret="pairing-secret-value",
        )
    )
    settings = Settings(
        hostname="lab host/unsafe",
        tailscale_auth_key="tailscale-secret-value",
        offsite_backup_access_key_id="access-key-id-secret",
        offsite_backup_secret_access_key="access-key-secret",
        webhook_destinations=[
            WebhookDestination(
                id="wh-001",
                url="https://hooks.example.test/ingest",
                auth_type="hmac",
                secret="webhook-secret-value",
                custom_headers={"Authorization": "Bearer custom-secret-value"},
            )
        ],
    )
    app.store.save_settings(settings)


class TestDiagnosticsBundleService:
    def test_redacts_known_secret_fields(self, app, data_dir):
        _seed_secret_state(app)
        (data_dir / "config" / ".secret_key").write_text("session-secret-value")
        (data_dir / "certs" / "server.key").write_text("tls-private-key-value")

        result = app.diagnostics_service.collect_sections(
            requested_by="admin",
            requested_ip="127.0.0.1",
        )
        try:
            members, manifest, files = _load_bundle(result.archive_path)
        finally:
            app.diagnostics_service.cleanup(result.run_id)

        users_json = next(
            text for name, text in files.items() if name.endswith("/config/users.json")
        )
        cameras_json = next(
            text
            for name, text in files.items()
            if name.endswith("/config/cameras.json")
        )
        settings_json = next(
            text
            for name, text in files.items()
            if name.endswith("/config/settings.json")
        )

        assert '"password_hash": "[REDACTED]"' in users_json
        assert '"totp_secret": "[REDACTED]"' in users_json
        assert '"recovery_code_hashes": "[REDACTED]"' in users_json
        assert '"pairing_secret": "[REDACTED]"' in cameras_json
        assert '"tailscale_auth_key": "[REDACTED]"' in settings_json
        assert '"offsite_backup_access_key_id": "[REDACTED]"' in settings_json
        assert '"offsite_backup_secret_access_key": "[REDACTED]"' in settings_json
        assert "hash-secret-value" not in users_json
        assert "pairing-secret-value" not in cameras_json
        assert "webhook-secret-value" not in settings_json
        assert all(".secret_key" not in member for member in members)
        assert all("certs" not in member for member in members)
        assert {entry["file"] for entry in manifest["redactions"]} == {
            "config/users.json",
            "config/cameras.json",
            "config/settings.json",
        }

    def test_rejects_concurrent_export_when_active_run_exists(self, app):
        svc = app.diagnostics_service
        assert svc._export_lock.acquire(blocking=False)
        with svc._state_lock:
            svc._active_run_id = "busy-run"
        try:
            with pytest.raises(DiagnosticsBundleError) as excinfo:
                svc.collect_sections(requested_by="admin", requested_ip="127.0.0.1")
        finally:
            with svc._state_lock:
                svc._active_run_id = ""
            svc._export_lock.release()

        assert excinfo.value.status_code == 429
        assert excinfo.value.error == "diagnostics_export_in_progress"

    def test_rate_limits_by_session_id(self, app):
        svc = app.diagnostics_service
        for _ in range(6):
            allowed, retry_after = svc.check_rate_limit("sess-123")
            assert allowed is True
            assert retry_after == 0

        allowed, retry_after = svc.check_rate_limit("sess-123")
        assert allowed is False
        assert retry_after > 0

    def test_skips_log_symlink_path_escape(self, app, data_dir):
        outside = data_dir / "outside-secret.txt"
        outside.write_text("outside-secret-value")
        link = data_dir / "logs" / "shadow-link"
        try:
            link.symlink_to(outside)
        except (NotImplementedError, OSError):
            pytest.skip("Symlinks are not available on this host")

        result = app.diagnostics_service.collect_sections(
            requested_by="admin",
            requested_ip="127.0.0.1",
        )
        try:
            _, manifest, files = _load_bundle(result.archive_path)
        finally:
            app.diagnostics_service.cleanup(result.run_id)

        logs_section = next(
            item for item in manifest["sections"] if item["name"] == "logs"
        )
        escaped = next(
            item
            for item in logs_section.get("files", [])
            if item["name"] == "shadow-link"
        )
        assert escaped["error"] == "path escape"
        assert all("outside-secret-value" not in text for text in files.values())
